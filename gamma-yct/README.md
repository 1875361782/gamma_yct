# gamma-yct

上海企业开办辅助：门户登录 → **Playwright 认 DOM 完成名称申报填报** → **点击一次下一步并验证跳转**。

名称申报 MVP 成功终态：`name_next_reached`（不代提交、不代签名）。

## 要求

- Python 3.10+
- Node 18+
- （可选）Playwright Chromium — 仅 `AUTOMATION_MODE=playwright` 需要

## 安装

```bash
cd gamma-yct
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
# 仅 playwright 模式需要：
playwright install chromium

cd apps/web
npm install
```

## 启动（开发）

终端 1 — API（工作目录必须是 `gamma-yct`）：

```bash
set AUTOMATION_MODE=dry_run
uvicorn apps.api.main:app --reload --host 127.0.0.1 --port 8000
```

终端 2 — Web：

```bash
cd apps/web
npm run dev
```

浏览器打开 Vite 提示的地址（默认 http://127.0.0.1:5173）。

前端为对话式 UI（参考 `材料/UI/`）：首页 → 智能对话收集 → 核验卡片 → 进度 → 完成页。  
**不收集随申办账号密码**。本次名称申报流程在本机可见浏览器中扫码，二维码默认不上传到后端。

终端 3 — Worker 自检：

```bash
python -m apps.worker
```

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `AUTOMATION_MODE` | `dry_run` | `dry_run` / **`playwright`** / `auto` / `desktop_rpa` |
| `ALLOW_FINAL_SUBMIT` | `false` | 一期必须为 false |
| `YCT_PORTAL_URL` | `https://yct.sh.gov.cn/portal_yct/` | **入口门户**（先登录） |
| `SKIP_QR_LOGIN` | `false` | `false`=门户扫码登录；`true`=要求已登录 |
| `PLAYWRIGHT_USE_CDP` | `false` | `true`=接入本机调试 Chrome（仍先门户） |
| `PLAYWRIGHT_CDP_URL` | `http://127.0.0.1:9222` | Chrome 远程调试地址 |
| `DESKTOP_RPA_FALLBACK` | `false` | 默认不回退坐标式桌面键鼠 |
| `YCT_LOGIN_URL` | 一网通办登录页 | redirect 回门户 |
| `PLAYWRIGHT_HEADLESS` | `false` | 建议有界面 |
| `YCT_CAPTURE_QR` | `false` | 本机可见浏览器默认不截图/上传二维码 |

### 推荐：门户登录 → Playwright 认元素填报

```powershell
.\scripts\start_api_playwright.ps1
# 流程：portal_yct → 扫码登录 → 名称申报 DOM 填报 → 单次下一步 → 验证跳转
```

另开一个 PowerShell 创建并启动固定名称申报案件：

```powershell
.\scripts\start_name_declaration.ps1
# 固定数据：上海市 / 盛表玛 / 科技 / 有限公司 / 有限公司
```

已打开调试 Chrome 且门户已登录：

```powershell
.\scripts\start_chrome_cdp.ps1   # 打开 portal_yct
$env:AUTOMATION_MODE="playwright"
$env:PLAYWRIGHT_USE_CDP="true"
$env:SKIP_QR_LOGIN="true"
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

**不要**在未登录时直接打开 online.yct 名称申报深链（易报错）。

## dry_run 验收（curl）

```bash
curl -s -X POST http://127.0.0.1:8000/api/cases
# 记下 caseId，PATCH 完整 payload 后：
curl -s -X POST http://127.0.0.1:8000/api/cases/{caseId}/validate
curl -s -X POST http://127.0.0.1:8000/api/cases/{caseId}/start
curl -s http://127.0.0.1:8000/api/cases/{caseId}
```

## 目录

- `apps/api` — FastAPI
- `apps/web` — Vue3 向导
- `apps/worker` — dry_run / playwright / **desktop_rpa**
- `apps/worker/desktop` — 桌面键鼠填报
- `data/cases` — 办件数据
- `docs/fields.md` — 字段勘探表
