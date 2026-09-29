# 电商 Agent 自建身份模块设计

日期：2026-09-29

状态：评审稿

关联设计：[电商 Agent MVP 架构与交易交接设计](2026-09-29-commerce-agent-mvp-design.md)。本稿定义 `identity/` 的账号、登录、令牌与撤销；购物会话、购物车归属及宿主交接仍以关联设计为准。

## 1. 背景、目标与边界

当前项目没有可接入的身份服务，现有推荐接口的 `user_id` 由调用方填写，不能证明请求者身份。电商 MVP 只服务已登录用户，跨购物会话共享一辆活动购物车，写车和结账交接必须绑定可信主体。因此本项目在同一 FastAPI 应用内建设独立的 `identity/` 模块，沿用 MySQL、SQLAlchemy 异步会话和 Alembic 迁移，不为身份另拆微服务。对购物域保留异步的 `IdentityProvider.verify(token) -> Principal` 边界，日后可替换签发方而不改变购物车所有权检查。

本期交付邮箱密码账号、邮箱验证、自助登录、访问令牌刷新、退出登录和密码重置；不交付社交登录、第三方 OAuth/OIDC 授权服务器、MFA、管理员控制台、邮箱修改或跨系统单点登录。开放注册、验证和找回密码依赖真实邮件投递；邮件未接通时只允许通过受控运维流程创建内部联调账号，不开放生产自助注册或找回，也不能以页面显示成功代替邮件送达。购物端点的生产上线须完成真实账号登录、验证和恢复链路，开发测试的固定 `Principal` 只能在测试配置使用。

## 2. 方案选择

| 方案 | 取舍 | 决定 |
| --- | --- | --- |
| 同应用 `identity/` + MySQL 账号/登录会话 + 短期 JWT + 轮换的不透明刷新令牌 | 复用现有部署和事务能力；保留 Bearer API 契约；需自行维护密钥、限流、邮件和撤销 | **采用**。访问令牌用于请求身份，服务端登录会话用于立即撤销。 |
| 纯无状态长寿命 JWT | 验证成本低，但退出、改密、禁用账号难以即时生效 | 不采用。 |
| 仅用浏览器 Cookie 会话 | 同站点浏览器简单，但需要改变既定的 Bearer 购物 API 契约 | 不作为本期购物 API 凭证。 |

此处 JWT 并非完整 OAuth/OIDC 服务；本系统只为自己的 API 签发访问令牌，不向第三方客户端提供通用授权能力。JWT 的签名证明令牌由本服务签发，不能代替服务端对账号和登录会话状态的检查。

## 3. 模块职责与数据模型

`identity/` 负责账号状态、密码验证、邮件令牌、登录会话、访问令牌签发与验证、刷新令牌轮换和审计。`api/auth.py` 负责 HTTP 契约、Cookie/CSRF、输入校验和错误映射。`api/commerce` 只接受 `require_principal` 返回的 `Principal`，不能从请求体、查询参数、普通自定义头或聊天文本提取用户身份。密码、令牌、重置链接和邮件验证码不进入 Agent 上下文。

新增表使用本项目的应用生成 UUID `CHAR(36)` 和 UTC `DATETIME(6)`；通过 Alembic 建表，不在应用启动时自动建表。

| 表 | 主要字段与约束 | 用途 |
| --- | --- | --- |
| `identity_users` | `id` 主键、`email_normalized` 唯一、`email_verified_at`、`password_hash`、`status=active/disabled`、`created_at`、`updated_at` | `id` 是不可变主体。邮箱不充当车归属键；本期不提供改邮箱接口。统一规范化规则为去首尾空白、Unicode NFC 与大小写折叠，不做特定邮箱商的点号或别名折叠。 |
| `identity_auth_sessions` | `id` 主键、`user_id` 外键、`created_at`、`last_used_at`、`expires_at`、`revoked_at`、`revoke_reason` | 一台设备的一次登录；每个访问令牌带 `sid`。禁用账号、退出、重置密码可立即使会话失效。建议绝对有效期 30 天，不因刷新无限延长。 |
| `identity_refresh_tokens` | `id` 主键、`auth_session_id` 外键、`token_hash` 唯一、`expires_at`、`consumed_at`、`revoked_at`、`created_at` | 只保存至少 256 位随机令牌的 SHA-256 哈希；保留已消费记录直至会话过期，以检测重放。每个会话同一时刻只有一个未消费且未撤销的刷新令牌；以仅对活动行生成会话 ID、对其他行生成 `NULL` 的生成列加唯一索引，并配合事务行锁保证。 |
| `identity_email_tokens` | `id` 主键、`user_id` 外键、`purpose=verify_email/reset_password`、`token_hash` 唯一、`expires_at`、`consumed_at`、`created_at` | 验证邮箱和重置密码分别使用至少 256 位随机的一次性令牌；新令牌发出时作废同用途旧令牌。 |
| `identity_mail_outbox` | `id` 主键、`user_id` 外键、`purpose`、`payload_ciphertext`、`status=pending/sent/failed`、`attempts`、`next_attempt_at`、`created_at` | 与账号/邮件令牌同事务入队；投递任务使用部署密钥解密邮件内容并限次重试，成功或令牌过期后删除密文。失败告警供运维修复。 |

不保存明文密码或明文刷新、验证、重置令牌。密码使用 Argon2id，参数至少满足 OWASP 当前建议的 `m=19456 KiB, t=2, p=1`，上线时按实例容量压测后可调高；每个密码由库生成独立盐。密码长度至少 15 个字符，允许至少 64 个字符及空格/Unicode，检查常见泄露密码；不要求数字、大小写和符号组合，也不要求定期修改。密码哈希版本/参数随哈希串保存，以便登录时逐步升级。

## 4. 主体与访问令牌

本服务配置稳定的 `issuer`，例如正式部署的身份域标识；`audience` 固定指向电商 API。`subject` 只使用 `identity_users.id`。`Principal(issuer, subject, principal_key)` 的 `principal_key` 为 `issuer` 与 `subject` 的 UTF-8 字节串分别加 4 字节大端长度前缀后串接，再取 SHA-256 十六进制摘要；写入购物会话、购物车和交接记录。未来更换 `issuer` 时必须迁移映射，不能直接改配置让旧车失联。邮箱不进入 `principal_key`。

登录成功或刷新成功签发 RS256 JWT，默认有效期 15 分钟，必含 `iss`、`aud`、`sub`、`sid`、`iat`、`nbf`、`exp`、随机 `jti`；JWT 头使用服务端维护的 `kid`。私钥通过部署密钥管理提供，不提交仓库或存入数据库；验签只使用服务端配置的公钥集合和固定 `RS256` 算法，不读取令牌自带的密钥 URL，不接受 `alg=none`。轮换时先分发新公钥，再切换签发私钥；旧公钥至少保留到旧访问令牌全部过期并越过时钟容差。生产配置缺失、密钥加载失败或时钟异常时受保护端点拒绝服务。

`require_principal` 对每个受保护请求验证签名、`kid`、`iss`、`aud`、时间声明、非空 UUID `sub` 和 `sid`，再查 MySQL，确认用户已验证且 `active`、登录会话属于该用户、未撤销且未过期。令牌即使还未到 `exp`，只要会话被撤销或账号被禁用就不能使用。凭证缺失、无效、过期或已撤销返回 401；数据库/密钥依赖故障返回 503，不把故障当作匿名身份。商品公开只读接口不要求登录。

## 5. 注册、登录与恢复流程

1. **注册**：`POST /api/v1/auth/register` 接收邮箱和密码，创建未验证账号，生成高熵一次性邮箱验证令牌，保存其哈希，并在同一事务写加密邮件发件箱。验证令牌默认 24 小时有效。只有邮件服务已配置、投递任务健康且能持久化发件任务才开放注册；响应表示请求已受理，不表示收件人已收到邮件。外部响应不泄露邮箱是否已存在。未验证账号不能取得购物访问令牌。邮件投递失败会重试和告警，并提供限流的重新发送验证邮件入口。
2. **验证邮箱**：`POST /api/v1/auth/verify-email` 提交一次性令牌；在事务中核对哈希、用途、时效、消费状态和账号，设置 `email_verified_at` 并消费令牌。重复或错误令牌不给出账号是否存在的信息。验证成功后用户再登录。
3. **登录**：`POST /api/v1/auth/login` 用规范化邮箱查账号，验证 Argon2id 哈希；失败统一返回模糊的凭证错误，避免枚举账号。成功后创建 `identity_auth_sessions` 和第一枚刷新令牌，响应短期 access token、过期时间，并设置刷新 Cookie。密码验证和限流在所有实例一致生效，不能只靠进程内计数。
4. **刷新**：`POST /api/v1/auth/refresh` 仅从刷新 Cookie 读取令牌，以其哈希定位记录；在同一 MySQL 事务中锁定登录会话和令牌行，核对未消费、未撤销、未过期和用户状态，消费旧令牌并保存新令牌；事务提交后才返回新 access token 和刷新 Cookie。刷新令牌的 `expires_at` 不得超过登录会话绝对到期时间。已消费令牌再次出现时撤销该登录会话及其所有刷新令牌，要求重新登录；未知随机令牌不得据此撤销别人的会话。客户端应把并发刷新收敛为单次请求，多标签页协调失败时以重新登录恢复。
5. **退出**：`POST /api/v1/auth/logout` 撤销当前登录会话及剩余刷新令牌并清除 Cookie；即使令牌已失效也清除浏览器 Cookie。密码重置、禁用账号撤销该用户全部登录会话。本期不提供已登录改密接口；需要换密码时走重置流程。
6. **找回密码**：`POST /api/v1/auth/password-reset/request` 对存在且已验证的邮箱生成一次性重置令牌并通过加密发件箱投递邮件，默认 30 分钟有效；无论邮箱是否存在都返回相同外部结果。`POST /api/v1/auth/password-reset/confirm` 在事务中验证并消费令牌、更新密码哈希、撤销全部登录会话；不自动登录。重置链接将令牌放在 URL fragment，由前端读取后通过请求体提交；服务端路由、访问日志、第三方请求和 Referer 不得包含令牌。

登录、注册、验证邮件、重置和刷新接口按账号/邮箱与可信客户端 IP 组合限流，计数存 Redis 并使用原子操作；Redis 不可用时这些接口拒绝服务，不能绕过限流。首版默认登录为每规范化邮箱每 15 分钟 5 次失败、每 IP 每 15 分钟 30 次失败；注册为每 IP 每小时 3 次；重发验证与重置申请为每邮箱每小时 3 次、每 IP 每小时 10 次；刷新为每会话每分钟 30 次。阈值可通过部署配置收紧；触发后返回 429 和 `Retry-After`。仅信任部署配置的反向代理提供的客户端 IP，不直接信任任意 `X-Forwarded-For`。失败次数、邮件发送和令牌重放写安全审计事件，但不记录密码、原始令牌、完整重置链接或敏感请求体。

## 6. 浏览器与 API 契约

默认宿主前端与 API 使用同站点 HTTPS。短期 access token 只保存在前端内存，调用购物 API 时放入 `Authorization: Bearer <access_token>`；刷新令牌只放 `__Host-` 前缀、`Secure`、`HttpOnly`、`SameSite=Lax`、`Path=/` 且无 `Domain` 的 Cookie，不放 `localStorage`/`sessionStorage`。页面重载后前端先请求 `GET /api/v1/auth/csrf`：服务端凭刷新 Cookie 定位有效会话，返回与该会话绑定的签名 CSRF 值；前端随后以自定义头提交该值调用 `/refresh`。登录时响应也返回当前会话的 CSRF 值。Cookie 会自动发送，因此登录、刷新、退出、注册和密码恢复的状态修改请求校验可信 `Origin`；刷新/退出额外校验 CSRF 值与 Cookie 所属会话一致。`SameSite` 只作附加防护。生产 CORS 明确列出宿主来源，不使用 `*`；跨站点部署需单独评审 Cookie、CSRF 和浏览器限制，不能直接把 `SameSite` 改为 `None`。

| 端点 | 输入 | 成功结果 |
| --- | --- | --- |
| `POST /api/v1/auth/register` | 邮箱、密码 | 通用受理结果；仅邮件链路可用时开放 |
| `POST /api/v1/auth/verify-email` | 一次性令牌 | 邮箱验证完成 |
| `POST /api/v1/auth/resend-verification` | 邮箱 | 通用受理结果；限流并作废旧验证令牌 |
| `POST /api/v1/auth/login` | 邮箱、密码 | access token、有效期、CSRF 值；设置刷新 Cookie |
| `GET /api/v1/auth/csrf` | 刷新 Cookie | 当前会话的 CSRF 值；不轮换令牌 |
| `POST /api/v1/auth/refresh` | 刷新 Cookie、CSRF 令牌 | 新 access token、有效期；轮换刷新 Cookie |
| `POST /api/v1/auth/logout` | 当前登录凭证、刷新 Cookie、CSRF 令牌 | 撤销会话并清除 Cookie |
| `POST /api/v1/auth/password-reset/request` | 邮箱 | 通用受理结果 |
| `POST /api/v1/auth/password-reset/confirm` | 一次性令牌、新密码 | 密码更新并撤销全部会话 |
| `GET /api/v1/auth/me` | Bearer access token | 当前用户 ID、已验证邮箱及账号状态 |

错误映射：无效凭证为 401，限流为 429，邮件/数据库/Redis/密钥依赖不可用为 503；注册和找回入口的外部响应不能泄露账号是否存在。所有认证响应设置禁止缓存，并避免把 access token 写入 URL。令牌刷新失败不沿用过期 access token 调用购物写端点。

## 7. 与购物及宿主的边界

购物 `POST /sessions` 创建独立的 24 小时购物会话，归属当前 `principal_key`；登录会话绝对有效期、15 分钟 access token 和购物会话有效期互不替代。访问令牌刷新后只要 `Principal` 相同，就能继续使用未过期购物会话；购物会话过期不删除购物车。所有车、动作、交接记录继续按 `principal_key` 在 API 层与服务层双重校验。旧推荐 API 的 `user_id` 保持兼容，但不得成为新交易入口的身份依据。

宿主读取交接快照仍需要独立服务凭证，并验证终端用户与 `checkout_handoffs.principal_key` 一致。本期 `identity/` 不提供通用服务到服务授权服务器；宿主结账接口、服务凭证及其轮换在交接集成时单独落实。不能把前端 JWT、`handoff_id` 或 URL 当作宿主服务身份。

## 8. 配置、验证与交付顺序

在 `config/settings.py` 增加稳定 `issuer`、`audience`、签名密钥来源及 `kid`、CSRF 签名密钥、发件箱加密密钥、访问令牌和登录会话时效、邮件发送器、允许的前端来源及限流配置；`.env.example` 只给占位符，不含真实密钥。生产启动检查必需密钥、HTTPS/来源配置、MySQL 和 Redis；缺项时认证与交易受保护端点不可用。开发/测试使用隔离数据库、受控邮件发送器与临时测试密钥，不允许测试替身配置进入生产。

按顺序交付：① 表结构、密码哈希、密钥加载和主体契约；② 登录/刷新/退出与即时撤销；③ 邮箱投递、验证、开放注册和重置；④ 购物端点 `require_principal` 接入与跨用户隔离；⑤ 宿主交接联调。邮件链路若未就绪，先以受控账号完成内部联调，开放注册和自助恢复保持关闭。

验收覆盖：错误密码与不存在账号的统一响应、未验证/禁用账号拒绝登录、跨用户读车与确认拒绝、JWT 错误签名/算法/受众/签发方/过期拒绝、退出与重置密码后旧 JWT 立即失效、刷新轮换及旧令牌重放撤销、并发刷新只有一次成功、限流依赖故障拒绝、邮件令牌单次消费与过期、Cookie/CSRF/CORS 配置，以及身份配置缺失时交易端点拒绝服务。端到端测试必须从真实登录取得 access token，不把固定 `Principal` 当作生产链路验证。

设计依据：[OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)、[OWASP Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)、[OWASP Forgot Password](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)、[NIST SP 800-63B](https://pages.nist.gov/800-63-4/sp800-63b/authenticators/)、[RFC 9700](https://www.rfc-editor.org/info/rfc9700/)。
