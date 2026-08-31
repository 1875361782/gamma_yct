# 伽马注册 · RPA 扫码登录演示包

> 一个**已端到端验证**的演示工程：用 Web RPA 自动操作「上海企业登记在线 / 一网通办」，
> 抓取**真实**登录二维码 → 通过 WebSocket 实时同步到手机 H5 扫码页 → 用户用「随申办」APP 扫码 → RPA 自动跳转。
>
> 用途：**给客户 / 投资人演示「手机 H5 提交 → Web 自动填报」闭环**，无需人工登录政务网。

---

## 0. 一句话启动（TL;DR）

```bash
cd 伽马注册_RPA演示包
bash run.sh
# 浏览器打开 http://localhost:8766/prototype  走屏 1~6，屏 6 内用随申办扫码即可
```

> 首次运行会联网自建 `.venv` + 安装 Playwright Chromium（约 150MB）；装好之后反复启动很快，
> 且 `?mock=1` 离线模式完全不需要政务网。
>
> 表单会自动保存在浏览器 localStorage；**重启后端后刷新页面即可继续填写**，无需重填。

---

## 1. 它解决什么问题

传统演示企业注册时，要手工登录一网通办、手工扫码，既慢又容易卡在登录环节。
本包把"获取二维码"这一步交给 RPA 浏览器自动完成，二维码实时投到手机 H5 页面，
用户只需用随申办扫码即可，演示流畅、可重复。

```
        B 页（手机 H5 · 用户扫码）           A 页（桌面 · RPA 操作端）
     ┌────────────────────────┐          ┌──────────────────────────┐
     │    真实登录二维码        │          │  RPA 浏览器（Playwright） │
     │    （RPA 同步过来）      │   WS     │  门户→关弹窗→开办→企业    │
     │  请使用随申办扫码        │ ←──────► │  →扫码登录→抓 QR →广播    │
     └────────────────────────┘ qr_update └──────────────────────────┘
                                       login_success（扫码后自动跳转）
```

---

## 2. 系统要求 & 网络说明

| 项 | 要求 |
|---|---|
| 操作系统 | macOS / Linux / Windows（WSL） |
| Python | 3.10 及以上（`run.sh` 会自动建 venv，不污染系统 Python） |
| 浏览器 | Playwright Chromium（首次运行 `run.sh` 自动安装，约 150MB） |
| 扫码工具 | 演示时准备手机「随申办」APP |
| 联网 | **真实模式**需能访问 `yct.sh.gov.cn`；**mock 模式**可完全离线 |

**网络说明（重要）**
- **首次 `bash run.sh`**：会 `pip install` 依赖 + `playwright install chromium`，需要联网。
- **之后再启动**：venv 与 Chromium 已缓存，启动很快、几乎不联网。
- **离线演示**：加 `?mock=1` 走本地测试站，无需政务网、无需联网即可完整演示扫码闭环。

---

## 3. 启动顺序（标准流程）

按此顺序操作，缺一不可：

1. **进目录**
   ```bash
   cd 伽马注册_RPA演示包
   ```

2. **一键启动**（自动完成：建 venv → 装依赖 → 装 Chromium → 起服务）
   ```bash
   bash run.sh
   ```
   - 想跳过浏览器安装（纯 mock 演示）：`bash run.sh --no-browser`
   - 想强制全局 mock：`bash run.sh mock`

3. **看终端输出确认** —— 出现以下信息即启动成功：
   ```
   ==================================================
     伽马注册 RPA 扫码登录演示
   ==================================================
     落地页:  http://localhost:8766/
     A 页:    http://localhost:8766/acquire
     B 页:    http://localhost:8766/qrlogin
     健康检查: curl http://localhost:8766/health
   ==================================================
   ```
   > 终端会一直挂着（服务在跑），**不要关掉这个终端窗口**。

4. **浏览器访问**（建议用 Chrome / Safari 打开下列地址）：
   - 先确认服务活着：`curl http://localhost:8766/health` → 返回 `{"status":"ok",...}`
   - 再开 `http://localhost:8766/prototype` 开始演示。

> ⚠️ 启动时若提示端口 8766 被占用：先 `lsof -i:8766` 找到占用进程并退出，或改 `run.sh` 里的 `--port 8766`。

---

## 4. 页面地址表

| 页面 | 地址 | 角色 | 模式 |
|---|---|---|---|
| 落地页 | http://localhost:8766/ | 入口，列出 9 屏原型 / A / B 三页 | — |
| **9 屏原型** | http://localhost:8766/prototype | 完整产品原型（屏 6 内嵌 B 页，自动触发 RPA） | **默认真实** |
| **A 页** | http://localhost:8766/acquire | RPA 操作端（桌面，监控用） | **默认真实** |
| **B 页** | http://localhost:8766/qrlogin | 扫码登录页（手机 H5） | **默认真实** |

> **默认即真实政务网**（上海企业登记在线）。只有在 URL 后加 `?mock=1`（或 `?mock=true`）
> 才走本地离线测试站。例如离线演示：`http://localhost:8766/prototype?mock=1`。

---

## 5. 演示流程

### 5.1 推荐：9 屏原型整合演示（方案 A，已落地）

1. 打开 **9 屏原型** `http://localhost:8766/prototype`，走完屏 1–5（手机端信息填报）。
2. 进入 **屏 6（扫码登录）** 时，原型内部 `<iframe>` 自动加载本包 B 页（`/qrlogin?sid=xxx`），
   并**用该 sid 触发后端 RPA**（B 页 `bind` 时后端自动启动 RPA，抓取**真实**二维码实时显示在屏 6 内）。
3. 用「随申办」APP 扫描屏 6 内显示的**真实二维码**。
4. 扫码成功 → B 页收到 `login_success` → `postMessage` 给原型父页面 → 原型**自动跳转屏 7（进度跟踪）**。
5. 可选：屏 6 内点 **「↗ 打开 A 页（监控）」**，在桌面端看 RPA 实时截图（同一 sid，不会重启 RPA）。

> 屏 6 的 RPA **唯一起动点是进入屏 6 的那一刻**（B 页 bind），不会提前启动导致二维码过期。
> 停留屏 6 期间后端会自动刷新二维码，始终是最新的。

### 5.2 独立双页演示（A/B 两页）

1. 打开 **A 页** `http://localhost:8766/acquire`，点「开始 RPA」。
   - A 页会显示 RPA 浏览器实时截图：打开门户 → 关闭政策解读弹窗 → 关闭重要提示 → 点「开办」→ 点「企业」→ 进入一网通办登录页 → 点「扫码登录」。
2. A 页进入登录页、**二维码出现后**，点 A 页右上角的 **「↗ 打开 B 页扫码」** 按钮
   （该按钮自动带上与 A 页**相同的 sid**，保证两页在同一会话）。
3. 在 **B 页** 用「随申办」APP 扫描显示的**真实二维码**。
4. 扫码成功 → A 页 RPA 浏览器检测到登录态并**自动跳转**；B 页弹出「扫码成功」。演示完成。

### sid 一致性（关键）
A、B 两页必须绑定**同一个 sid** 才能共享同一个 RPA 会话。
**务必用页面上的「互跳按钮」在两个页面间跳转**，不要手动改 URL，否则会生成两个独立会话、二维码对不上。
（也可手动在 URL 显式写同一个 `?sid=xxx`。）

---

## 6. 两种运行模式

| 模式 | 触发方式 | 说明 |
|---|---|---|
| **真实**（默认） | 直接 `http://localhost:8766/prototype` 或 `/acquire` | RPA 真的打开 `yct.sh.gov.cn/portal_yct/`，跑完整流程抓真实 QR。需联网政务网。 |
| **离线 Mock** | URL 加 `?mock=1` | 后端用本地假登录页，秒返二维码，用于内网 / 无网演示。WS 连通与 `login_success` 事件均可验证。 |

- Mock 模式触发登录成功：访问 `http://localhost:8766/mock/index.html?sid=<你的sid>`，点页面上的「模拟扫码」按钮。
- 离线完整演示路径：`bash run.sh --no-browser` 启动，然后开 `http://localhost:8766/prototype?mock=1`。

---

## 7. 目录结构

```
伽马注册_RPA演示包/
├── run.sh                  # 一键启动（自建 venv + 装依赖 + 装 Chromium + 起服务）
├── README.md               # 本文档
├── backend/
│   ├── app.py              # FastAPI：页面路由 + WebSocket /ws/{sid} + 落地页 + /prototype
│   ├── main.py             # uvicorn 入口
│   ├── sessions.py         # sid ↔ RPASession 路由表（同 sid 多监听器 / 状态补发）
│   ├── protocol.py         # WS 消息工厂（bound/qr_update/login_success/step/screenshot…）
│   ├── requirements.txt    # 依赖清单（fastapi/uvicorn/websockets/playwright）
│   ├── rpa/
│   │   ├── browser.py      # Playwright 流程：门户→关弹窗→开办→企业→扫码登录→抓QR→监控登录
│   │   ├── qr_capture.py   # 二维码捕获（img / canvas 兜底扫描 + 二次校验取稳定帧）
│   │   └── login_monitor.py# 登录态监听（避免门户页误报）
│   ├── mock_gov/index.html # 本地假政务网登录页（mock 模式用）
│   ├── static_prototype.html # 9 屏原型（同源托管，屏 6 内嵌 B 页，默认真实站）
│   ├── static_acquire.html # A 页（RPA 操作端前端）
│   └── static_qrlogin.html # B 页（手机 H5 扫码登录前端，扫码成功会 postMessage 父页面）
└── 验证截图/               # 端到端验证截图（登录页 + 二维码捕获）
```

> 说明：本包不包含 `.venv` 与 Chromium 二进制，`run.sh`（macOS / Linux）或 `run.bat`（Windows）
> 会在首次运行时自动创建，二者启动行为一致。
> 若需**离线分发整机包**：在本机对应系统跑通一次（生成 `.venv` 与浏览器）后整体打包即可。
> 注意：venv 与 Chromium 是**操作系统 / 架构锁定的**（macOS-arm64 ≠ Windows-x64），
> **macOS 打的整机包不能在 Windows 上直接跑**，反之亦然——需在目标系统上各打各的。
> Windows 版 `run.bat` 已设 `PLAYWRIGHT_BROWSERS_PATH=0`，Chromium 装进 `.venv` 内，整包可跨同架构 Windows 离线拷贝。

---

## 8. 最近修复（页面状态 ↔ 显示图片同步）

之前出现过「A 页卡在第 6 步『选择企业』，但 RPA 实际已到第 7 步登录页」的不同步问题。
根因是**点击后页面尚未落定就去抓图**，导致显示停留在旧页面。修复方式（已生效）：

- 点击「企业」后**固定等待页面落定（timing）**，再二次校验把 RPA 当前页切到真实登录页 —— 显示的图永远跟点击后的真实页面一致。
- 抓到二维码后**重抓一次取稳定帧**（二次校验），避免把半渲染的图片发出去。
- A 页保留「✓ 我已到登录页，继续」手动接管按钮作为兜底（仅在自动同步异常时使用）。

---

## 9. 健康检查与排错

```bash
# 服务是否正常
curl http://localhost:8766/health
# 期望返回 status:"ok"，并列出 active_sessions / pages
```

| 现象 | 排查 |
|---|---|
| 打开页面显示「连接中…」一直不变 | 后端没起 / 端口被占；检查 `run.sh` 终端日志 |
| A 页卡在「打开门户」 | 网络无法访问政务网；改用 `?mock=1` 演示 |
| B 页一直转圈无二维码 | A、B 页 sid 不一致；用「互跳按钮」重开两页 |
| 扫码后 A 页画面不动 | 已修复：登录成功后截图流继续；若仍异常，看 A 页日志 |
| Chromium 启动失败 | `bash run.sh` 会自动安装；公司网络可手动 `python -m playwright install chromium` |
| 端口 8766 被占用 | `lsof -i:8766` 查占用进程退出，或改 `run.sh` 的 `--port` |

---

## 10. 与「9 屏 H5 原型」整合（方案 A 已实现）

> 目标：用户在前 5 屏填完信息，**第 6 屏直接就是本包的 B 页（扫码登录）**，扫码成功后进第 7 屏。

**已落地：方案 A · 屏 6 内嵌 B 页（iframe）**。原型屏 6 用 `<iframe src="/qrlogin?sid=xxx">` 直接加载本包 B 页；
sid 由原型 `enterPhaseB()` 生成，并用于触发后端 RPA（本包 `/ws/{sid}` 在 B 页 `bind` 时已自动起 RPA）。
B 页收到 `login_success` 后 `window.parent.postMessage(...)` 给原型父页面，原型监听该消息后自动 `scannedOK()` 进屏 7。

- 三个 HTML（原型 / A 页 / B 页）均由后端**同源托管**，靠 sid 联动，无 iframe / WS 跨域问题。
- RPA **唯一启动点 = 进入屏 6 的那一刻**（B 页 bind），不会提前启动导致二维码过期；停留期间自动刷新二维码。
- A 页降级为屏 6 出现后的「监控端」：用屏 6 内的「↗ 打开 A 页（监控）」按钮打开（自动带同 sid），加入已存在会话、**不重启 RPA**。

---

## 11. 验证结果

- ✅ 真实门户端到端跑通：门户 → 关弹窗 → 开办 → 企业 → 登录页 → 扫码登录 → 捕获真实二维码（200×200 base64）。
- ✅ 双页同 sid 下，扫码成功 A/B 两页同步显示；登录后 A 页画面持续刷新不中断。
- ✅ mock 模式可离线验证 WS 连通与 `login_success` 事件。
- ✅ **9 屏原型整合（方案 A）浏览器 E2E 通过**：进入屏 6 后 iframe 自动挂载 B 页并触发 RPA、屏 6 内显示真实二维码；模拟扫码后 B 页 `postMessage` → 原型**自动跳转屏 7**，全程无 console / page 报错。
- 验证截图见 [`验证截图/`](验证截图/)。

---

## 12. 已知限制 / 待办

- [ ] `yct.sh.gov.cn` 改版时需更新 `rpa/browser.py` 的流程选择器与 `qr_capture.py` 的 QR 选择器。
- [ ] 多 sid 并发受 Chromium context 数限制（默认约 50）。
- [ ] 生产前需补充：QR 与 sid 绑定校验、登录态加密落库、风控/限速。
- [ ] 「开办 → 企业」弹窗为当前实测路径，若政务网交互变更需回归测试。

---

## 13. Windows 运行说明

后端 Python 代码**跨平台**，Playwright / FastAPI 均支持 Windows。本包已附带 `run.bat` 作为 Windows 原生启动器。

### 13.1 前置条件
- 安装 **Python 3.10+**，安装时务必勾选 **「Add python.exe to PATH」**（否则 `run.bat` 找不到 Python）。
- 建议用 **PowerShell 或 命令提示符（CMD）** 运行；直接**双击 `run.bat`** 也能跑（会弹出终端窗口）。

### 13.2 启动顺序（Windows）
1. 进入解压后的目录（例如 `伽马注册_RPA演示包 2`）。
2. 双击 **`run.bat`**（或命令行 `run.bat`）。
   - 首次运行会：建 `.venv` → `pip install` 依赖 → `playwright install chromium`（约 150MB，**需联网**）→ 起服务。
   - 纯离线演示：`run.bat --no-browser`（不装 Chromium，配合 `?mock=1` 页面用）。
3. 终端出现落地页地址即启动成功，**不要关闭该窗口**。
4. 浏览器打开 `http://localhost:8766/prototype` 开始演示。

> 首次启动 Windows 可能弹出**防火墙提示**，请允许 Python 通过「专用网络」（否则本机浏览器访问不了 localhost）。
> 之后再次启动无需联网（依赖与 Chromium 已缓存），`?mock=1` 模式完全离线。

### 13.3 离线整机包（发给别的 Windows 同事）
`run.bat` 已设 `PLAYWRIGHT_BROWSERS_PATH=0`，Chromium 会装进 `.venv` 内。所以在**一台 Windows 机器上先跑通一次 `run.bat`** 后，
把整个目录打包发给同事，**对方双击 `run.bat` 即可零联网运行**（前提是同架构 Windows，如都是 64 位）。

### 13.4 跨平台提醒
- **macOS 打的离线包不能在 Windows 跑**（Chromium 二进制不兼容）；Windows 同理。
- 想要双平台离线包：分别在 macOS 和 Windows 各跑一次启动脚本生成各自环境的 `.venv`，再分别打包。
