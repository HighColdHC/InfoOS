# 可选的双因素边缘网关

此模板用于自备 Nginx（含 auth_request 模块）与 Authelia 4.39 的同机部署；不是已部署实例。参考 [Authelia 官方 Nginx 集成](https://www.authelia.com/integration/proxies/nginx/)。所有域名、证书名和路径须由使用者替换，仓库不附账号、密码、Token 或可用密钥。

1. Appliance 使用 [Docker 配置](../docker/.env.example)，产品端口保持 `127.0.0.1:8443`，只开放边缘 HTTPS 443。网关与 Appliance 不同机时，后端防火墙只允许网关；使用受限可信网络与验证证书的 TLS。CDP、VNC、OpenCLI daemon、debug 和 Authelia 9091 不开放公网。
2. 把 [nginx.conf.example](nginx.conf.example) 作为独立 Nginx 配置起点；把 [proxy-common.conf](proxy-common.conf) 安装至 `/etc/nginx/infoos-edge/proxy-common.conf`。将 `infoos.example.com`、`auth.example.com`、`example.com` 换为自己的域名，提供匹配的 HTTPS 证书。将 Appliance 的受信 CA/自签名证书安装至 `appliance-ca.pem`，将 `APPLIANCE_CERTIFICATE_NAME` 换为该证书的 SAN 名称。保持上游证书校验，不忽略证书错误。
3. 用户在受保护环境自行生成至少 32 字符随机共享密钥，在本地受限文件 `/etc/nginx/infoos-edge-secret.conf` 中写 `set $infoos_edge_secret "<用户生成的密钥>";`；同一值放入 Appliance 受保护环境的 `INFOOS_EDGE_READER_SECRET`。不得把示意占位符当密钥。空值时模板返回 503；文件及 `.env` 均设为 0600，不入 Git、不打印内容。设置 `INFOOS_PUBLIC_BASE_URL=https://infoos.example.com` 并按部署手册重建容器；已有实例先预览再切换。
4. 将 [configuration.yml.example](configuration.yml.example) 安装为 `/etc/authelia/configuration.yml`；用自己的用户数据库 `/etc/authelia/users.yml`（官方 file backend 格式，密码使用用户生成的 Argon2id 哈希）。分别生成独立随机秘密文件，以 `AUTHELIA_SESSION_SECRET_FILE`、`AUTHELIA_STORAGE_ENCRYPTION_KEY_FILE`、`AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET_FILE` 注入 Authelia，不复用边缘密钥。Authelia 运行用户须能读取受限文件；数据库与通知目录设为 0700，通知文件含注册链接，勿公开。完成自己的 TOTP 注册；部署通知服务及账户恢复由用户自行配置。
5. 用所安装版本执行 `authelia validate-config --config /etc/authelia/configuration.yml`（使用同一秘密文件环境）与 `nginx -t -c <配置绝对路径>`，成功后按自己的服务管理流程启用。模板没有单因素兜底策略，未知域名默认拒绝。

全站 `location /` 保护管理面、`/browser-console/`（含 WebSocket）、`/r/`、阅读 API、`/assets/` 与卡片预览附件，以及本地登录路径的全部方法。Authelia 验证成功后 Nginx 用服务器配置覆盖 `X-InfoOS-Edge-Auth`，清空客户端的内部身份头与 Authorization；客户端伪造同名头不能替代双因素。Console 仍需从面板签发的限时 grant 和同源请求。只有 Authelia 会话 cookie 被剔除，其余 Cookie 和上游 Set-Cookie 保留；若修改 Authelia cookie 名，也须同步修改两个 map。

`/mcp`（含子路径）、`/api/plugin/v1/` 与 `/api/capture/v1/` 不经过 Authelia，保留请求的 Bearer Authorization，由产品按各自 scope 验证；边缘清空它们的 Cookie、共享密钥和内部身份头，不以边缘认证替代 Bearer。`/api/internal/` 与 `/_infoos_` 不对外开放。

验收时分别验证：匿名访问管理面、Console、阅读和附件要求双因素；完成双因素后可使用；伪造身份头仍被拒绝；Console 无 grant/过期/跨源被拒绝；MCP、插件与捕获接口无 Bearer 或缺少 scope 被拒绝、合法最小 scope 请求可达且不跳登录页。模板静态检查不代表这些真实请求已经通过。

不用双因素网关时的最低要求：产品端口只绑定 loopback 或由防火墙限制的可信局域网，使用 HTTPS、强管理员密码与表单会话、最小 scope Token；不得将管理面或 Console 直接暴露公网。内部端口不发布，同源检查不能替代认证。详见 [部署手册](../docker/README.md)。
