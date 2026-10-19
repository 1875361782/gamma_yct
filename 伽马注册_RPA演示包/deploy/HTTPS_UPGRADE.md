# 域名 + HTTPS 升级步骤

一期 HTTP（公网 IP）稳定后再做。无需重装 ECS。

## 1. DNS

1. 取得域名，例如 `banli.example.com`。
2. 添加 **A 记录** → ECS 弹性公网 IP，TTL 10 分钟。
3. 等待解析生效：`nslookup banli.example.com`。

## 2A. 阿里云免费证书 + Nginx

1. 控制台 → 数字证书管理（免费版）→ 申请并完成域名验证。
2. 下载 Nginx 格式，放到：
   - `D:\gamma\certs\fullchain.pem`
   - `D:\gamma\certs\privkey.key`
3. 编辑 [`nginx/gamma-https.conf`](nginx/gamma-https.conf)：替换 `banli.example.com` 与证书路径。
4. 用该配置替换/覆盖当前 HTTP server（或 include）。
5. 安全组增加 **TCP 443**。
6. `nginx -t` → `nginx -s reload`。
7. 访问 `https://banli.example.com/prototype`；80 应 301 到 HTTPS。

证书到期前在控制台续签，替换 pem/key 后 reload。

## 2B. Caddy 自动 HTTPS

1. 使用 [`Caddyfile.https`](Caddyfile.https)，改成真实域名。
2. 安全组放行 80、443。
3. 启动 Caddy；自动申请并续期 Let’s Encrypt。

## 3. 回归

- [ ] Network：API 与 `wss://` 均同域 HTTPS
- [ ] 无再请求裸 IP / localhost
- [ ] 扫码 WS、上传、短信、签名刷码再跑一遍
- [ ] `/admin` 仍需 Admin Token

## 4. 换机（可选）

1. 新 ECS 按部署手册装好并同步 `gamma-yct\data\cases`。
2. 改 A 记录，或把弹性公网 IP 解绑旧机、绑到新机（域名可不改）。
