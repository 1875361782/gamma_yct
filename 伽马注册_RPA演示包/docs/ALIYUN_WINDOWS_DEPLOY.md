# 阿里云 Windows Server 部署手册

目标：单台 Windows ECS 跑演示包 uvicorn（8766）+ 同进程 gamma-yct RPA；一期公网 **HTTP**；后续域名 + **HTTPS**。

仓库需保持兄弟目录：

```text
D:\gamma\
  伽马注册_RPA演示包\
  gamma-yct\
```

相关脚本与配置在 [`../deploy/`](../deploy/)。

---

## 0. 本机准备

1. 确认 GitHub 已有最新代码（`main`）。
2. 准备生产 `backend\.env`（参考 `backend\.env.example`）：
   - `GAMMA_HEADLESS=1`
   - `GAMMA_ADMIN_TOKEN` / `GAMMA_BOOTSTRAP_SECRET`
   - `GAMMA_SMS_*`（短信控制台业务须已开通）
3. 记下办公出口公网 IP（用于收紧 RDP）。

---

## 1. 开通 ECS（控制台操作清单）

1. 地域：华东 2（上海）或就近。
2. 实例：约 **4 vCPU / 8 GiB**（如 ecs.c7.xlarge）；系统盘 ESSD **80–100 GiB**。
3. 镜像：**Windows Server 2022 数据中心版（中文）**。
4. 网络：分配 **弹性公网 IP**，带宽 5–10 Mbps。
5. 安全组入方向：
   - TCP **80** 来源 `0.0.0.0/0`（HTTP）
   - TCP **3389** 来源 **仅你的办公 IP/32**
   - **不要**放行 8766
6. （推荐）系统盘自动快照：每日，保留 7 天。
7. 记下：公网 IP、Administrator 初始密码、安全组 ID。

登录后立即改密；时区设为 **UTC+8**。

### 安装软件

1. Google Chrome 稳定版  
2. Python 3.11 或 3.12（勾选 Add python.exe to PATH）  
3. Git for Windows（推荐，便于 `git pull`）  
4. Nginx for Windows 或 Caddy（见第 3 节）  
5. [NSSM](https://nssm.cc/)（注册 Windows 服务）

Windows 防火墙：允许入站 TCP 80；8766 仅本机。

---

## 2. 部署代码

在 RDP 中（PowerShell / CMD）：

```bat
mkdir D:\gamma
cd /d D:\gamma
git clone https://github.com/1875361782/gamma_yct.git repo
:: 若仓库根已含两个包，调整为：
::   D:\gamma\伽马注册_RPA演示包
::   D:\gamma\gamma-yct
:: 可用 mklink /J 或直接把 clone 内容整理成上述兄弟结构

cd /d D:\gamma\伽马注册_RPA演示包
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.venv\Scripts\python.exe -m pip install -r ..\gamma-yct\requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
```

也可直接运行：

```bat
deploy\install-deps.bat
```

将生产 `.env` 放到 `伽马注册_RPA演示包\backend\.env`（**不要**用 Git 提交密钥）。

冒烟：

```bat
deploy\start-uvicorn.bat
```

另开窗口：

```bat
curl http://127.0.0.1:8766/health
```

期望：`status=ok`，`headless=true`，短信开通后 `smsConfigured=true`（若 health 未暴露该字段，可用 bootstrap 或发码接口验证）。

---

## 3. HTTP 反代（80 → 8766）

1. 将 [`deploy/nginx/gamma.conf`](../deploy/nginx/gamma.conf) 拷入 Nginx `conf` 并 `include`，或替换 `nginx.conf` 的 server 段。
2. 启动 Nginx，监听 80。
3. 浏览器验证：`http://公网IP/prototype`、`/health`、`/admin`。

Caddy 备选：[`deploy/Caddyfile.http`](../deploy/Caddyfile.http)。

---

## 4. 注册 Windows 服务（NSSM）

以管理员打开 CMD：

```bat
cd /d D:\gamma\伽马注册_RPA演示包\deploy
install-nssm-services.bat
```

或手动：

```bat
nssm install GammaUvicorn D:\gamma\伽马注册_RPA演示包\.venv\Scripts\python.exe
nssm set GammaUvicorn AppDirectory D:\gamma\伽马注册_RPA演示包
nssm set GammaUvicorn AppParameters -m uvicorn backend.app:app --host 127.0.0.1 --port 8766 --log-level info
nssm set GammaUvicorn AppEnvironmentExtra GAMMA_HEADLESS=1
nssm set GammaUvicorn AppStdout D:\gamma\logs\uvicorn.out.log
nssm set GammaUvicorn AppStderr D:\gamma\logs\uvicorn.err.log
nssm start GammaUvicorn
```

Nginx 同样用 NSSM 注册为 `GammaNginx`。日志目录：`D:\gamma\logs\`。

---

## 5. 上线验收清单

见 [`deploy/ACCEPTANCE_CHECKLIST.md`](../deploy/ACCEPTANCE_CHECKLIST.md)。通过后停掉本机 8766 / cloudflared，避免双端抢案件。

---

## 6. 后续：域名 + HTTPS

见 [`deploy/HTTPS_UPGRADE.md`](../deploy/HTTPS_UPGRADE.md)。

---

## 7. 日常维护摘要

| 场景 | 操作 |
|------|------|
| 探活 | `curl http://127.0.0.1:8766/health` |
| 看板 | `http://公网IP/admin` + Admin Token |
| 重启 | `nssm restart GammaUvicorn` |
| 更新 | 停服务 → `git pull` → `install-deps.bat` → 启服务 |
| 备份 | 快照 + `robocopy` `gamma-yct\data\cases` |
| 回滚 | 关安全组 80 / 停 Nginx；快照恢复 |

并发：按单机单任务运维。
