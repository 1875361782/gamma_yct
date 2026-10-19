# 公网 HTTP 验收清单

前提：`GammaUvicorn` 已起，Nginx/Caddy 反代 80→8766，安全组放行 80。

将 `PUBLIC` 换成公网 IP（或域名）。

## 基础

- [ ] `curl http://PUBLIC/health` → `"status":"ok"`
- [ ] health 中 `"headless": true`（或 `headless_forced: true`）
- [ ] `http://PUBLIC/prototype` 可打开产品页
- [ ] 浏览器 Network：请求走 `PUBLIC`，**无** `localhost:8766`

## 鉴权与管理

- [ ] 未带 Admin Token 时，公网调 `/api/admin/dashboard` 被拒绝（或按设计要求 token）
- [ ] 配置 `GAMMA_ADMIN_TOKEN` 后，`http://PUBLIC/admin` 可看看板

## 短信

- [ ] 注册页「获取验证码」成功（或明确报阿里云业务错误，而非演示模式）
- [ ] 若失败：查 `D:\gamma\logs\uvicorn.err.log`（如 `isv.OUT_OF_SERVICE`）

## 填报与签名（真实政务网，耗时）

- [ ] 提交信息 → 扫码页出现二维码
- [ ] 扫码成功 → 进度页推进
- [ ] 材料提交到达进度跟踪相关成功态后，「继续完成签名」可出支付宝码
- [ ] 人脸码阶段可刷码；完成后进入办理完成页
- [ ] 桌面**无**可见 Chrome 窗（无头）
- [ ] 「取消」后对应 Chrome 进程退出

## 切流

- [ ] 本机 8766 / cloudflared 已停止
- [ ] 仅 ECS 承接生产流量
