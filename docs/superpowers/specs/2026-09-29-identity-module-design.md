# 电商 Agent 身份模块设计（演示版）

日期：2026-09-29

状态：评审稿

关联设计：[电商 Agent MVP 架构与交易交接设计](2026-09-29-commerce-agent-mvp-design.md)。本稿定义 `identity/` 如何产生和验证可信主体；购物会话、购物车归属及宿主交接仍以关联设计为准。

## 1. 目标和演示边界

项目目前没有身份服务，旧推荐请求的 `user_id` 由调用方填写，不能证明请求者身份。购物 MVP 要按真实主体隔离会话、购物车和确认动作。本期在同一 FastAPI 应用中建设 `identity/` 模块，沿用 MySQL、SQLAlchemy 异步会话和 Alembic；向购物域提供异步的 `IdentityProvider.verify(token) -> Principal`。

**演示版的邮箱只是登录名，不验证邮箱所有权。** 注册提交邮箱和密码，在本地创建账号；登录用密码校验该账号，不发送邮件，不接 SMTP，不发送验证码，也没有“验证邮箱”“忘记密码”接口。演示注册仅在显式 `identity_demo_mode=true` 的隔离环境开放；正式环境必须拒绝这种自助注册。演示版能验证“持有已注册账号密码的人”，不能证明其控制该邮箱，也不能作为对外开放的真实用户账号系统。需要真实用户注册时，另设计邮箱所有权验证和账号恢复流程。本稿不要求实现这些流程。

这里的“模拟”仅限账号来源和邮箱验证：密码哈希、登录校验、令牌签发与验证、会话撤销、主体归属均走真实代码与持久化数据。不得用任意邮箱、请求体 `user_id`、固定开发主体或跳过密码检查来签发购物访问令牌。测试可注入受控 `Principal`，但生产配置不能启用测试替身。

## 2. 方案及模块边界

| 方案 | 取舍 | 决定 |
| --- | --- | --- |
| 本地演示账号 + 短期 JWT + 服务端登录会话 | 无需邮件服务；保留 Bearer API 契约并可验证跨用户隔离；演示账号没有邮箱归属证明 | **采用**，仅供内部演示和联调。 |
| 固定令牌或前端直接传 `user_id` | 简单，但任何调用方都能冒充其他用户 | 不采用。 |
| 完整邮箱注册、验证和找回 | 可支持真实邮箱归属和恢复，但需要邮件投递、重试和滥用防护 | 留给真实用户上线阶段单独设计。 |

`identity/` 管理演示账号、密码哈希、登录会话、访问令牌、刷新令牌、撤销和安全审计；`api/auth.py` 管理 HTTP、Cookie、CSRF、输入和错误映射；`api/commerce` 只使用 `require_principal` 返回的 `Principal`。Agent 不接触密码、原始令牌或账号查询工具。JWT 只用于本项目 API，不代表本项目提供通用 OAuth/OIDC 授权服务。

## 3. 数据模型和主体

表由 Alembic 创建；主键使用应用生成的 UUID `CHAR(36)`，时间使用 UTC `DATETIME(6)`。不在应用启动时自动建表。

| 表 | 字段与约束 | 用途 |
| --- | --- | --- |
| `identity_users` | `id` 主键、`email_normalized` 唯一、`password_hash`、`status=active/disabled`、`source=demo`、`created_at`、`updated_at` | `id` 是不可变主体；邮箱只作登录名，不标记为已验证，也不作为购物车归属键。规范化规则是去首尾空白、Unicode NFC 和大小写折叠，不做邮箱商特定的点号或别名折叠。 |
| `identity_auth_sessions` | `id` 主键、`user_id` 外键、`created_at`、`last_used_at`、`expires_at`、`revoked_at`、`revoke_reason` | 一次设备登录。建议绝对有效期 30 天，不因刷新无限延长；退出或禁用账号后失效。 |
| `identity_refresh_tokens` | `id` 主键、`auth_session_id` 外键、`token_hash` 唯一、`expires_at`、`consumed_at`、`revoked_at`、`created_at` | 不透明随机令牌至少 256 位；只存 SHA-256 哈希。已消费行保留至会话到期，用于发现重放。每个会话最多一枚活动令牌，以活动行生成列唯一索引和事务行锁保证。 |

密码使用 Argon2id 哈希，至少配置 `m=19456 KiB, t=2, p=1`，库为每个密码生成独立盐；密码或原始刷新令牌不落库、不入日志。密码至少 15 个字符，允许至少 64 个字符及空格/Unicode，不要求固定字符组合。演示版没有自助找回密码；忘记密码只能在隔离环境通过受控运维命令重置，重置时撤销该用户全部登录会话，不能由前端任意指定账号执行。

本服务配置稳定 `issuer` 和仅指向电商 API 的 `audience`，`subject` 使用 `identity_users.id`。`Principal(issuer, subject, principal_key)` 中的 `principal_key` 对 `issuer` 和 `subject` 的 UTF-8 字节分别加 4 字节大端长度前缀，串接后取 SHA-256 十六进制摘要；邮箱不参与。该键写入购物会话、购物车和交接记录。更换 `issuer` 时必须迁移主体映射，不能直接修改配置使旧车失联。

## 4. 注册、登录、刷新与验证

1. **演示注册**：`POST /api/v1/auth/register` 仅在 `identity_demo_mode=true` 且部署环境标记为 `development/test/demo` 时可用；提交邮箱和密码，规范化邮箱并保存 `source=demo` 的账号和密码哈希。邮箱重复返回冲突；页面明确标记“演示账号，邮箱未验证”。接口不发送邮件，也不自动登录。正式环境即使误设开关也拒绝启动或拒绝该端点。
2. **登录**：`POST /api/v1/auth/login` 按邮箱查账号并验证密码。不存在账号、密码错误和禁用账号对外使用相同的凭证错误，避免枚举。成功后创建服务端登录会话和第一枚刷新令牌；事务提交后返回短期 access token 并设置刷新 Cookie。正式环境不接受 `source=demo` 的账号登录。
3. **访问令牌**：登录或刷新签发固定 `RS256` 的 JWT，默认有效期 15 分钟，含 `iss`、`aud`、`sub`、`sid`、`iat`、`nbf`、`exp`、随机 `jti`，头部带本服务配置的 `kid`。私钥由部署密钥管理提供；只按服务端配置的公钥集合和固定算法验签，不接受 `alg=none` 或令牌指定的密钥 URL。轮换时先分发新公钥，再切换签发私钥；旧公钥保留至全部旧令牌过期并越过时钟容差。
4. **每次请求验证**：FastAPI `require_principal` 提取 Bearer 令牌，经 `IdentityProvider.verify` 校验签名、`kid`、`iss`、`aud`、时间字段、非空 UUID `sub` 和 `sid`，再查 MySQL，确认账号与登录会话匹配、状态有效、未撤销且未过期。即使 JWT 尚未过期，退出或禁用账号也应立即生效。无效凭证返回 401；数据库或密钥故障返回 503，不降级为匿名或固定主体。
5. **刷新**：`POST /api/v1/auth/refresh` 从刷新 Cookie 取令牌，以哈希定位记录；事务内锁会话与令牌，核对账号状态、时效和消费状态，消费旧令牌并保存新令牌，提交后返回新 access token 和刷新 Cookie。刷新令牌不能超过登录会话绝对到期时间。已消费令牌重放时撤销该会话；未知令牌不能触发对其他会话的撤销。客户端收敛并发刷新；若多标签页竞争导致撤销，重新登录。
6. **退出**：`POST /api/v1/auth/logout` 撤销当前登录会话和所有剩余刷新令牌，清除 Cookie。账号禁用和受控密码重置撤销该用户全部登录会话。

登录与注册按规范化邮箱和可信客户端 IP 在 Redis 中原子限流；演示默认登录每邮箱每 15 分钟 5 次失败、每 IP 每 15 分钟 30 次失败，注册每 IP 每小时 3 次，刷新每会话每分钟 30 次。触发后返回 429 与 `Retry-After`。Redis 不可用时这些端点拒绝服务。仅信任配置的反向代理提供的客户端 IP；不直接信任任意 `X-Forwarded-For`。安全审计记录失败次数、撤销和重放，不记录密码或原始令牌。

## 5. 浏览器及 HTTP 契约

默认前端与 API 在同站点 HTTPS。前端只在内存中保存 access token，调用购物 API 时发送 `Authorization: Bearer <access_token>`。刷新令牌放在带 `__Host-` 前缀、`Secure`、`HttpOnly`、`SameSite=Lax`、`Path=/`、无 `Domain` 的 Cookie，不放 `localStorage` 或 `sessionStorage`。登录响应给出当前会话的 CSRF 值；页面重载后先调用 `GET /api/v1/auth/csrf`，服务端凭刷新 Cookie 定位有效会话，返回与之绑定的签名 CSRF 值，再调用 `/refresh`。登录、注册、刷新、退出检查可信 `Origin`，刷新和退出还校验自定义头里的 CSRF 值。生产 CORS 仅允许明确配置的前端来源，不用 `*`。

| 端点 | 请求 | 成功结果 |
| --- | --- | --- |
| `POST /api/v1/auth/register` | 邮箱、密码；仅演示环境 | 演示账号 ID；无邮件、无自动登录 |
| `POST /api/v1/auth/login` | 邮箱、密码 | access token、过期时间、CSRF 值；设置刷新 Cookie |
| `GET /api/v1/auth/csrf` | 刷新 Cookie | 当前会话的 CSRF 值 |
| `POST /api/v1/auth/refresh` | 刷新 Cookie、CSRF 值 | 新 access token；轮换刷新 Cookie |
| `POST /api/v1/auth/logout` | 登录凭证、刷新 Cookie、CSRF 值 | 撤销会话并清除 Cookie |
| `GET /api/v1/auth/me` | Bearer access token | 用户 ID、登录邮箱、`email_verified=false`、账号来源 |

所有认证响应禁止缓存，不把令牌放 URL；请求体和日志不得记录密码、刷新令牌。Cookie 只用于认证端点，购物端点仍要求 Bearer。浏览器演示环境也使用 HTTPS，以保证刷新 Cookie 的 `Secure` 和 `__Host-` 属性有效。

## 6. 购物边界、验证和后续升级

购物会话建议 24 小时有效，按 `principal_key` 归属；它不等于登录会话。刷新 access token 后同一主体可继续未过期购物会话，购物会话过期不删除购物车。所有车、动作和交接记录在 API 与服务层按主体双重校验。旧推荐接口的 `user_id` 保持兼容，但绝不能作为新交易入口的身份依据。宿主读取交接快照仍需独立服务凭证，不能把用户 JWT、`handoff_id` 或 URL 当作宿主服务身份。

配置包含稳定 `issuer`、`audience`、签名密钥来源与 `kid`、CSRF 签名密钥、令牌/会话时效、允许来源、Redis 限流和 `identity_demo_mode`；`.env.example` 仅保留占位符。正式环境禁止演示账号登录及自助注册，缺少密钥、MySQL、Redis 或可信前端来源时受保护端点不可用。开发/测试使用隔离数据库、临时密钥和演示账号；固定 `Principal` 只用于单元测试。当前设计不将演示注册视为生产身份交付。

验收覆盖：注册无邮件依赖、邮箱重复与并发唯一约束、错误密码/不存在/禁用账号的统一失败响应、演示模式在正式环境不可用、JWT 错误签名/算法/受众/签发方/过期拒绝、退出与密码重置后旧 JWT 立即失效、刷新轮换和旧令牌重放、跨用户读车/确认/交接拒绝、Redis 故障拒绝、Cookie/CSRF/CORS 配置。端到端演示从注册和密码登录取得 access token；不能用固定 `Principal` 冒充完整身份链路。

真实用户上线另需设计邮箱所有权验证、账号恢复、滥用处置与运维支持。引入这些能力前，不对外宣称邮箱已验证，也不把演示账号视为可供真实交易使用的生产身份。演示账号及其购物车不自动迁移为正式账号；若未来需要迁移，须另设计主体映射和归属证明。安全选择参考 [OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html) 与 [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)。
