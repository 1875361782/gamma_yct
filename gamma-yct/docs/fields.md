# yct 字段勘探表（2026-08-06 真机）

对照上海企业登记在线；Playwright 主路径见 `apps/worker/playbooks/`。

## 微前端 iframe

| 业务 | frame URL 片段 | 备注 |
|------|----------------|------|
| 名称申报 | `web-name-apply` | `#/name-declaration` |
| 设立基本信息 | `web-reg-apply` | `#/enterprise-info-new` |

宿主：`https://online.yct.sh.gov.cn/web-yct-master/...#/wujieViews/A1`

## 名称申报

| 字段 | 定位 | 备注 |
|------|------|------|
| 名称区划 | placeholder=`请选择名称区划` | 选「上海」 |
| 名称字号 | placeholder=`请输入名称字号` | 生僻字会出拼音 popper |
| 行业表述 | placeholder=`请输入行业表述` | 自动完成点首项 |
| 组织形式 | placeholder=`请选择组织形式` | `.orgDescPopper`：内资有限公司 + 有限公司 |
| 拼音 | `.nameFontsizePopper label.el-radio` | 如 `sheng4 biao3 ma3` |
| 企业名称 | `label.el-radio` 含全称 | 如 上海盛表玛科技有限公司 |
| 下一步 | button 文本「下一步」 | |
| 告知书/承诺书 | `.el-dialog` 内 checkbox | 勾选后点确定；可能二次「请确认已知晓」 |

## 设立基本信息

| 字段 | 定位 | 示例/规则 |
|------|------|-----------|
| 经办人姓名 | form-item「经办人姓名」 | 法人姓名 |
| 移动电话 | form-item「移动电话」 | |
| 证件类型 | 下拉 | 默认居民身份证 |
| 证件号码 | form-item「证件号码」 | |
| 经办人类型 | 下拉 | **法定代表人** |
| 申请机关 | 下拉 | `{区}市场监督管理局` |
| 受理点 | placeholder=`请选择受理点` | 机关选定后选第一项 |
| 企业类型 | 下拉 | 一人公司→`有限责任公司(自然人独资)` |
| 经营期限 | 下拉 | `不约定期限` |
| 住所 | form-item「住所」 | 完整地址 |
| 联系电话 | form-item「联系电话」 | |
| 章程决议日期 | placeholder 日期 | 默认当天 |
| 是否合并分立 | radio「否」 | |
| 身份证上传 | `input[type=file]`×2，按「人像面/国徽面」定位 | `set_input_files`；截图 `id-uploaded.png` |
| 经营范围 | 点「选择经营范围」/`.yd-tips` → 搜关键词 → 勾选 → 确定 | 关键词来自 `company.businessScope` + 备选「科技」；结果见 `fill-scope-result.json` |
| 暂存 | button「暂存」 | 成功文案「暂存成功」；**禁止点最终提交** |

## 环境变量

| 变量 | 说明 |
|------|------|
| `AUTOMATION_MODE=playwright` | 真机 |
| `YCT_NAME_APPLY_URL` | 覆盖名称申报入口 |
| `PLAYWRIGHT_CHANNEL=chrome` | 使用本机 Chrome |
| `ALLOW_FINAL_SUBMIT=false` | 一期必须 |
