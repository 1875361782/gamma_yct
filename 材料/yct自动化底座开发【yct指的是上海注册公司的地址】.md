# yct 自动化操作底座 — PRD

**产品名称**：yct Automation Platform（政务 UI 自动化底座）  
**版本**：v1.0 草案  
**日期**：2026-05-21  
**范围说明**：本文档**仅**描述「如何操作一网通办网站」与「如何统一获取操作反馈」的底座能力，不包含伽玛注册前端、Agent 对话、开办全流程产品设计。

---

## 1. 产品定位

### 1.1 一句话

将 **yct.sh.gov.cn（一网通办）** 上的政务办理操作，封装为**可编排、可查询、结果结构化**的统一自动化能力；对外不提供「假 API」，对内用 **Playbook + 浏览器执行器** 驱动真实网页。

### 1.2 要解决的核心问题

| 问题 | 说明 |
|------|------|
| 无开放接口 | 登记机关未提供核名/设立等 HTTP API，只能操作网页 |
| 操作方式分散 | 各业务线若各自写爬虫/脚本，登录、会话、截图、解析重复建设 |
| 反馈不统一 | 成功/失败/进行中在页面上是文案、弹窗、跳转，业务系统难以消费 |
| 页面易变 | 选择器失效、弹窗未知，需要 Playbook 版本与兜底机制 |

### 1.3 底座交付物（本 PRD 边界）

1. **操作 yct**：按 Playbook 在浏览器中完成指定流程（登录、导航、填表、提交、翻页等）。  
2. **得到反馈**：将页面终态解析为**统一任务状态 + 结构化 result**，并可选留存截图/日志。

**不在本文档范围**：伽玛品牌 UI、客户填表字段设计、开办 8 步业务编排、计费、客服话术等（由上层业务系统调用本底座）。

---

## 2. 目标用户

| 角色 | 使用方式 |
|------|----------|
| **业务系统研发** | 调用统一任务 API，传入 `flowId` + `payload`，轮询至终态 |
| **自动化运维** | 维护 Playbook、代办账号、执行模式、监控失败率 |
| **专员/运营** | 处理 `need_human`（验证码、UKey）、复核截图 |

---

## 3. 产品目标

### 3.1 目标（Must Have）

- 提供**统一任务模型**：创建任务 → 异步执行 → 查询状态 → 终态结果。  
- 支持**多流程 Playbook 注册**（首期落地 `name_self_declaration` 名称自主申报）。  
- 反馈字段**固定契约**：`internalStatus`、`customerMessage`、`result`、`artifacts`（截图等）。  
- 支持**代办账号**会话（凭证仅服务端，不暴露给调用方）。  
- 执行可追溯：至少记录任务 ID、时间线、关键截图路径。

### 3.2 非目标（Won't Have）

- 不封装 yct 不存在的「官方 REST API」。  
- 不保证任意页面 100% 全自动（验证码等必须支持人工介入态）。  
- 不在底座内实现具体业务规则引擎（如伽玛 Agent 对话逻辑）。  
- 不替代 Playwright 主路径；Computer Use 仅作异常兜底（可配置）。

---

## 4. 核心概念

| 概念 | 定义 |
|------|------|
| **Target System** | 目标政务系统，首期固定 `yct.sh.gov.cn` |
| **Flow / Playbook** | 某一类网页办理流程的配置包：`flowId`、入口 URL、步骤、选择器、结果判定规则 |
| **Task** | 一次自动化执行实例，由 `taskId`（或 `caseId` + `stepId`）标识 |
| **Payload** | 调用方传入的业务数据（如名称四段式），Playbook 负责映射到页面字段 |
| **Internal Status** | 底座内部细粒度状态（如 `yct_name_checking`） |
| **Normalized Feedback** | 对调用方暴露的统一响应（状态 + 文案 + 结构化 result） |
| **Artifact** | 截图、HTML 片段、申报号等留痕物 |

---

## 5. 功能需求

### 5.1 统一任务 API

底座对上层提供**与流程无关**的同一套接口形态（实现可复用现有 `server/src/routes/cases.js` 模式）。

| 能力 | 方法 | 说明 |
|------|------|------|
| 提交任务 | `POST /api/v1/tasks` 或 `POST .../cases/{id}/steps/{step}` | Body 含 `flowId`、`payload`、`automation` 元数据 |
| 查询任务 | `GET /api/v1/tasks/{taskId}` | 返回当前 `internalStatus` 与 `result` |
| 确认人工完成 | `POST .../customer-action` | 验证码/刷脸完成后继续执行 |
| 健康检查 | `GET /health` | 返回 `automationMode`、版本 |

**提交请求（逻辑字段）**

```json
{
  "schemaVersion": "1.0",
  "taskType": "SH_NAME_VERIFICATION",
  "caseId": "GM2505210001",
  "stepId": "name",
  "channel": "shanghai_yct",
  "payload": { },
  "automation": {
    "targetSystem": "yct.sh.gov.cn",
    "flowId": "name_self_declaration",
    "operatorMode": "playwright"
  }
}
```

**查询响应（逻辑字段）**

```json
{
  "internalStatus": "yct_name_passed",
  "customerMessage": "名称已通过",
  "result": {
    "approvedName": "上海云图科技有限公司",
    "applicationNo": "……",
    "checkedAt": "2026-05-21T10:00:00Z"
  },
  "artifacts": {
    "screenshots": ["./data/screenshots/GM2505210001-name-pass.png"]
  },
  "updatedAt": "2026-05-21T10:00:00Z"
}
```

### 5.2 操作 yct（执行引擎）

执行引擎负责把 Playbook **翻译为浏览器动作**。

| 步骤类型 | 说明 | 实现优先级 |
|----------|------|------------|
| `goto` | 打开 URL | P0 |
| `login` | 填账号密码、点登录 | P0 |
| `click` / `fill` / `select` | 按选择器操作表单 | P0 |
| `submit` | 提交申报 | P0 |
| `wait` | 等待网络/元素/固定时长 | P0 |
| `assert_text` | 判定页面出现成功/失败文案 | P0 |
| `screenshot` | 关键节点全页截图 | P0 |
| `retry` | 备选数据（如备选字号）循环 | P1 |
| `cua_fallback` | 选择器失败时交 Computer Use | P2 |

**会话要求**

- 每任务独立 BrowserContext，或按代办账号池复用（待技术方案）。  
- 超时：单任务默认 10 分钟，可配置。  
- 失败时**必须**截图并写入 `artifacts`。

### 5.3 统一反馈（结果解析层）

无论 yct 页面用何种 UI 表达，底座**向外**只输出下列几类终态与中态。

**中态（进行中，可继续轮询）**

| internalStatus | 含义 |
|----------------|------|
| `pending` / `queued` | 已接收 |
| `agent_starting` / `browser_ready` | 启动浏览器 |
| `yct_login` | 登录中 |
| `yct_name_form_filling` | 填表中（示例，按 flow 扩展） |
| `yct_name_checking` | 已提交，等待比对 |
| `yct_form_filling` / `yct_reviewing` | 其它流程填表/审核 |

**终态（停止轮询）**

| internalStatus | 含义 | 调用方映射建议 |
|----------------|------|----------------|
| `yct_name_passed` / `completed` | 流程目标达成 | `success` |
| `failed` | 明确失败（含驳回文案） | `failed` |
| `need_customer_action` / `need_human` | 需人工（验证码等） | `need_action` |
| `cancelled` | 任务取消 | `cancelled` |

**result 结构（按 flowId 约定，底座校验 schema）**

| flowId | 成功字段 | 失败字段 |
|--------|----------|----------|
| `name_self_declaration` | `approvedName`, `applicationNo?` | `reason`, `failedField?`, `attemptedNames?` |
| （扩展）`company_establishment` | `ok`, `scopeReview?` | `reason` |

**解析规则（Must）**

1. 优先 **DOM 选择器 / 固定文案** 判定，不单独依赖 LLM 判断成败。  
2. 无法判定时 → `need_human`，附截图，**不得**伪报成功。  
3. 所有 `failed` 必须带 `result.reason`（页面原文或摘要）。

### 5.4 Playbook 管理

| 需求 | 说明 |
|------|------|
| 注册表 | `flowId` → Playbook 模块（JS/JSON/YAML） |
| 版本 | `playbookVersion`，改版可并存灰度 |
| 选择器 | 集中配置，支持多套 fallback 选择器 |
| 入口 URL | `loginUrl`、`entryUrl` 环境变量注入，禁止写死生产密码 |

首期 Playbook：`name_self_declaration`（名称自主申报），字段映射见 `server/src/playbooks/name-self-declaration.js`。

### 5.5 执行模式

| 模式 | 用途 |
|------|------|
| `dry_run` | 不调 yct，按时间线模拟状态，供联调 |
| `playwright` | 生产默认：脚本驱动真实页面 |
| `computer_use` | 脚本失败或 `assert` 不确定时，截图 + CUA 决策（可选） |

环境变量：`AUTOMATION_MODE`、`YCT_*` 账号与 URL、`SCREENSHOT_DIR`。

### 5.6 人工介入

当页面出现验证码、UKey、滑块、未知弹窗时：

1. 任务置 `need_customer_action`（或 `need_human`）。  
2. `customerMessage` 说明需要做什么。  
3. 调用方 `POST customer-action` 后，Worker **从断点或指定步骤** 继续（P1 可先简化为「从检查点重跑」）。

---

## 6. 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    调用方（任意业务系统）                   │
└───────────────────────────┬─────────────────────────────┘
                            │ 统一 Task API
┌───────────────────────────▼─────────────────────────────┐
│  API 层          接收请求、鉴权（预留）、返回 Normalized   │
├─────────────────────────────────────────────────────────┤
│  调度层          队列、并发限制、任务状态机持久化            │
├─────────────────────────────────────────────────────────┤
│  Playbook 层     flowId 路由、步骤定义、结果 schema        │
├─────────────────────────────────────────────────────────┤
│  执行层          Playwright Runner │ CUA Fallback        │
├─────────────────────────────────────────────────────────┤
│  反馈层          DOM 解析、截图、result 组装、artifacts    │
└───────────────────────────┬─────────────────────────────┘
                            │ HTTPS（网页）
┌───────────────────────────▼─────────────────────────────┐
│              yct.sh.gov.cn（无官方 API）                   │
└─────────────────────────────────────────────────────────┘
```

**模块与现有代码映射**

| 模块 | 路径（当前仓库） |
|------|------------------|
| API | `server/src/routes/cases.js` |
| 调度/状态 | `server/src/store.js`、`server/src/statuses.js` |
| Playbook | `server/src/playbooks/*.js` |
| 执行+反馈 | `server/src/worker/name-verification.js` |

---

## 7. 典型流程示例：名称自主申报

```text
调用方 POST task(flowId=name_self_declaration, payload=名称字段)
    → pending → yct_login → yct_name_form_filling
    → fill & submit → yct_name_checking
    → 解析页面：
         通过 → yct_name_passed + result.approvedName
         驳回 → failed + result.reason（若有备选则重试）
         不明 → need_human + screenshots
    → 调用方 GET 直至终态
```

---

## 8. 非功能需求

| 类别 | 要求 |
|------|------|
| 可靠性 | 单步骤可重试；任务失败必有 `reason` + 截图 |
| 安全 | 政务账号仅服务端；调用方不可下发密码 |
| 审计 | 任务 ID、状态变迁时间、截图路径可检索 |
| 可维护 | 页面改版仅改 Playbook；`flowId` 不变则 API 不变 |
| 性能 | 队列排队；单账号并发上限可配置（防风控） |
| 观测 | `/health` + 日志；后续：失败率、平均耗时指标 |

---

## 9. 验收标准

### 9.1 P0（底座可联调）

- [ ] `dry_run` 下提交 `name_self_declaration`，GET 能拿到 `yct_name_passed` 或 `failed` 及完整 `result`  
- [ ] 状态变迁符合第 5.3 节枚举，无「悬空」状态  
- [ ] 失败任务 `result.reason` 非空  

### 9.2 P1（真机操作 yct）

- [ ] `playwright` 模式使用真实 `YCT_LOGIN_URL` / 账号完成至少 1 次名称比对  
- [ ] 成功时 `approvedName` 与页面一致；截图已写入 `artifacts`  
- [ ] 驳回时返回页面原文或摘要到 `reason`  

### 9.3 P2（统一能力完善）

- [ ] 新增第二个 `flowId` 无需改 API，仅注册 Playbook  
- [ ] `need_human` → `customer-action` → 继续执行闭环  
- [ ] Playbook 版本号与选择器失效日志  

---

## 10. 风险与依赖

| 风险 | 缓解 |
|------|------|
| yct 页面改版 | Playbook 版本 + 监控 + 快速热更新选择器 |
| 风控/验证码 | `need_human` + 专员队列 |
| 误判通过/失败 | 双通道判定（DOM 文案 + 截图人工复核策略） |
| 法务合规 | 代办资质、操作留痕、调用协议 |

**依赖**：Node 18+、Playwright Chromium、伽玛 yct 代办账号、专员配合录制首版 Playbook。

---

## 11. 附录：与上层业务的关系

```text
伽玛注册 / 其它 ERP  →  只关心「办件 + 步骤 + 客户文案」
                              ↓
                    yct 自动化底座（本文档）
                              ↓
                         yct 网页
```

上层传入 `payload`，底座返回 **Normalized Feedback**；上层负责展示、对话、开办流程顺序，**不负责**选择器维护与浏览器会话。

---

**文档维护**：产品/平台 · **关联实现**：`/server/README.md`
