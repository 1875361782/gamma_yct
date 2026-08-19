# Playbooks

| flowId | 文件 | 说明 |
|--------|------|------|
| portal | `apps/worker/playbooks/portal.py` | 门户入口：开办 → 企业 → 扫码登录 → 设立登记 |
| qr_login | `apps/worker/playbooks/qr_login.py` | 随申办扫码截图辅助 |
| fill_pipeline | `apps/worker/playbooks/fill_pipeline.py` | 编排：核名 → 基本信息 → 出资 → 人员 → 开办 → 暂存 |
| basic_info | `apps/worker/playbooks/basic_info.py` | 比对弹窗、身份证 OCR、住所、经营范围、暂存/下一步 |
| capital | `apps/worker/playbooks/capital.py` | 出资信息（认缴等） |
| personnel | `apps/worker/playbooks/personnel.py` | 董事/高管/联络员 + 受益所有人 |
| opening | `apps/worker/playbooks/opening.py` | 办理方式 → 五险一金 → 公章刻制 → 开户预约 → 暂存 |
| selectors | `apps/worker/playbooks/selectors.yaml` | DOM 文案配置 |

## 标准业务路径（到开办暂存）

1. 打开 [portal_yct](https://yct.sh.gov.cn/portal_yct/)
2. 关闭弹窗 →「开办」→「企业」→「扫码登录」（随申办）
3. 「我已知晓上述内容」→「企业设立登记（不涉及前置审批事项）」
4. **核名**：字号 / 行业 / 组织形式 →「下一步」
5. **基本信息**：比对弹窗、经办人/身份证、住所、经营范围 → 暂存/下一步
6. **出资** → **人员**（含受益所有人）→「已完成，进入下一步」
7. **开办**：办理方式 → 五险一金 → 公章刻制（自费/暂不刻章）→ 开户预约 → **暂存**
8. 状态 `ready_for_user_submit`；**不**自动提交/签名（`ALLOW_FINAL_SUBMIT=false`）

```powershell
cd "d:\工作\伽马账\gamma-yct"
.\.venv\Scripts\python.exe scripts\run_name_flow.py --case GM202608062231EF --no-wait
```

`ALLOW_FINAL_SUBMIT` 必须为 `false`。
