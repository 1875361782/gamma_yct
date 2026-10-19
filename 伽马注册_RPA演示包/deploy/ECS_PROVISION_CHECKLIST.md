# ECS 开通检查表（控制台逐步勾选）

在阿里云控制台完成下列项后，再 RDP 执行 `docs/ALIYUN_WINDOWS_DEPLOY.md` 第 2 节起。

## 实例

- [ ] 地域：华东 2（上海）或就近
- [ ] 规格：≥ 4 vCPU / 8 GiB
- [ ] 镜像：Windows Server 2022 数据中心版（中文）
- [ ] 系统盘：ESSD ≥ 80 GiB
- [ ] （可选）数据盘：40–100 GiB，挂载后用于 `D:\gamma-data`
- [ ] 带宽：5–10 Mbps + 弹性公网 IP
- [ ] 自动快照：系统盘每日，保留 ≥ 7 天

## 安全组

- [ ] 入站 TCP 80，来源 `0.0.0.0/0`
- [ ] 入站 TCP 3389，来源 `你的办公IP/32`（不要 `0.0.0.0/0`）
- [ ] **未**开放 TCP 8766
- [ ] （HTTPS 阶段）入站 TCP 443

## 登录与基础软件

- [ ] RDP 可登录；已修改 Administrator 密码
- [ ] 时区 UTC+8
- [ ] 已装 Google Chrome
- [ ] 已装 Python 3.11/3.12（PATH）
- [ ] 已装 Git（推荐）
- [ ] Windows 防火墙允许 TCP 80
- [ ] 已记下公网 IP：`________________`

## 目录约定

- [ ] `D:\gamma\伽马注册_RPA演示包`
- [ ] `D:\gamma\gamma-yct`
- [ ] `D:\gamma\logs`
- [ ] （HTTPS）`D:\gamma\certs`
