# 外网访问：Cloudflare Tunnel（只暴露产品页）

目标：外网用户只能打开产品首页（表单 → 二维码 → 进度），**看不到**本机 Playwright 抓码/填报窗口。RPA 仍在你这台跑 8766 的机器上以**无头**方式运行。

```text
外网用户 ──HTTPS──► Cloudflare Tunnel ──► 本机 127.0.0.1:8766
                                              │
                                              ▼
                                    无头 Chrome 填报 yct.sh.gov.cn
```

## 0. 前置

1. 本机已能启动演示后端：`http://127.0.0.1:8766/`
2. 本机能访问 `yct.sh.gov.cn`
3. 已安装 [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/)

## 1. 启动本机后端（强制无头）

```bat
cd 伽马注册_RPA演示包
.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8766
```

说明：

- 绑定 `127.0.0.1` 即可（隧道连本机回环）；不必把 8766 对局域网开放。
- `/api/start-automation` **忽略**客户端 `headless`，服务端强制无头。
- 可选共享口令：`set GAMMA_API_TOKEN=你的长随机串`（与产品页 bootstrap token 二选一即可通过鉴权）。
- A 页监控默认不对公网开放；本机调试可设 `GAMMA_ALLOW_MONITOR=1`。

## 2. 配置 Cloudflare Tunnel

### 快速体验（临时公网 URL）

```bat
cloudflared tunnel --url http://127.0.0.1:8766
```

终端会打印一个 `https://xxxx.trycloudflare.com`，用**手机流量**（非当前 Wi‑Fi）打开验证。

### 固定域名（推荐给客户）

1. Cloudflare Zero Trust → Networks → Tunnels → Create
2. 安装并登录 `cloudflared`，按控制台提示创建隧道
3. Public Hostname 指到 `http://127.0.0.1:8766`
4. 用户入口只发：`https://你的域名/`（不要发 `/acquire`）

建议再开 **Access 应用策略**（邮箱 OTP / 共享密码），避免完全裸奔。

## 3. 验证清单（必须用非局域网）

| 步骤 | 期望 |
|------|------|
| 手机流量打开公网 `/` | 看到 9 屏产品原型，不是旧落地页卡片 |
| 填完前几屏进入扫码 | 屏 6 出现二维码；本机**无**可见 Chrome 窗口 |
| 随申办扫码 | 进入进度屏；进度文案更新 |
| 直接访问公网 `/acquire` | 404（监控页关闭） |
| DevTools → Network | API/`wss` 均走同一公网域名，无请求 `localhost:8766` |

## 4. 安全边界

- **会暴露**：产品 HTML、`/api/*`、WebSocket `/ws/{sid}`、隐藏 iframe 用的 `/qrlogin`
- **不暴露**：`gamma-yct/data/cases/**/screenshots`（未挂静态目录）；本机桌面 Chrome；RDP
- 写接口需页面先调 `/api/bootstrap` 领取短时 token（或配置 `GAMMA_API_TOKEN`）
- 本机休眠/断网 = 外网不可用；要 7×24 需另迁固定出口机器

## 5. 管理页（任务与进度）

本机打开：`http://127.0.0.1:8766/admin`

- 列表来源：内存中的 relay 会话 + `gamma-yct/data/cases` 近期案件
- 展示：案件号、企业名称、状态、当前阶段、进度文案、是否运行中/待扫码
- 默认每 2 秒自动刷新
- **本机**可直接访问 API；**公网**需设置环境变量后再带口令：

```bat
set GAMMA_ADMIN_TOKEN=请换成足够长的随机串
```

管理页输入框填入口令并点「保存口令」（写入浏览器 localStorage，请求头 `X-Gamma-Admin-Token`）。

不要把 `/admin` 当产品入口发给终端用户；产品入口仍是 `/`。

## 6. 故障排查

| 现象 | 处理 |
|------|------|
| 公网页 API 打到 localhost | 硬刷新；确认使用隧道域名打开，不要下载 HTML 用 file:// |
| 401 unauthorized | 刷新页面重新 bootstrap；检查系统时间 |
| 有头 Chrome 又弹出来 | 确认已重启后端；服务端应固定 `headless=True` |
| WS 连不上 | 隧道需支持 WebSocket；用 `wss://`（HTTPS 站点自动） |
| 经营范围/核名拉取失败 | 公网页会直连 `https://api.gamaz.cn`（不依赖本机访问上游）；手机强刷隧道页。本机调试仍走 8766 代理 |
