# 电商 Agent 身份模块实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现可供内部演示的邮箱密码账号与可信 `Principal`，让后续购物交易端点能用短期 Bearer 令牌识别用户并校验资源归属。

**Architecture:** 在现有 FastAPI 应用内新增 `identity/`；MySQL 保存账号、登录会话与刷新令牌，Redis 负责分布式限流。注册的邮箱仅作演示登录名；密码校验、JWT 验证、即时撤销与刷新轮换是真实路径。`api/identity.py` 提供统一 `require_principal`，后续购物会话/购物车服务只消费主体，不依赖演示注册细节。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2.x async、MySQL 8、Alembic、`redis.asyncio`、`argon2-cffi`、`PyJWT[crypto]`、现有 `cryptography`、pytest/httpx。使用仓库 `.venv/bin/python` 运行测试；隔离 MySQL 使用现有 `ECOM_TEST_DATABASE_URL`，Redis 集成测试新增 `ECOM_TEST_REDIS_URL`。

**Spec:** `docs/superpowers/specs/2026-09-29-identity-module-design.md`；购物主体接口还须对照 `docs/superpowers/specs/2026-09-29-commerce-agent-mvp-design.md` 第 6 节。

**Status:** 待实施计划评审；本文件只规划开发，不表示身份模块已实现。

## Global Constraints

- 演示邮箱只是登录名，不验证所有权；不实现 SMTP、邮件验证码、邮箱验证、找回密码或通用 OAuth/OIDC 服务。
- `identity_demo_mode=true` 只可在 `development/test/demo` 环境使用；`production` 不得注册或登录 `source=demo` 账号。固定 `Principal` 只用于测试。
- 密码只存 Argon2id 哈希，至少 `m=19456 KiB, t=2, p=1`、每密码独立盐；不落库或记录明文密码、原始刷新令牌。
- 身份表由 Alembic `0004_identity` 迁移建立；UUID `CHAR(36)`、UTC `DATETIME(6)`；应用启动不自动建表。迁移测试必须用独立 MySQL，跳过不算验收。
- Access JWT 固定 `RS256`、默认 15 分钟、必含 `iss/aud/sub/sid/iat/nbf/exp/jti` 与受控 `kid`；每个受保护请求还要检查 MySQL 用户和登录会话，失效即 401，依赖故障即 503。
- 刷新令牌至少 256 位，数据库只存 SHA-256 哈希；30 天登录会话绝对到期，刷新轮换、重放撤销及单活动令牌由事务和约束保证。
- 浏览器 access token 只在内存；刷新令牌用同站点 HTTPS 的 `__Host-`、`Secure`、`HttpOnly`、`SameSite=Lax` Cookie。认证写接口检查 `Origin`，刷新/退出校验 CSRF；CORS 不用 `*`。
- 现有推荐与公开商品只读接口保持原契约；新交易端点不得使用请求体 `user_id` 确定主体。当前没有购物车端点，本计划不实现购物车。

## Review Focus

这五种输入/故障容易漏掉，已分别落入下列任务的测试：

1. Unicode/大小写等价邮箱并发注册只产生一名用户，密码哈希与原始密码不泄露（任务 1、3）。
2. JWT 的 `kid`、算法、签发方、受众或时间声明被篡改/遗漏时拒绝，受控公钥轮换仍可验证未过期旧令牌（任务 2、4）。
3. 未知刷新令牌不能据其猜测的会话 ID 撤销别人；已消费令牌重放必须撤销自己的会话（任务 4）。
4. Redis 故障不能绕过限流；伪造 `X-Forwarded-For` 不能改变客户端 IP；演示开关在正式环境不能开放注册/登录（任务 3、5）。
5. Cookie、CSRF、Origin 或数据库故障不能让受保护路由退化为匿名/固定主体，公开报价和旧推荐仍可用（任务 5）。

---

## 文件与职责

| 路径 | 职责 |
| --- | --- |
| `identity/models.py`、`migrations/versions/0004_identity.py`、`migrations/env.py` | 三张身份表、外键/唯一约束、活动刷新令牌生成列及 Alembic 注册。 |
| `identity/types.py`、`identity/passwords.py`、`identity/tokens.py` | `Principal`/结果 DTO、Argon2id 包装、受控 RS256 签发/验签。 |
| `identity/rate_limit.py`、`identity/csrf.py` | Redis 原子限流、与登录会话绑定的 CSRF 签名与校验。 |
| `identity/service.py` | 演示注册、密码登录、令牌刷新、退出、逐请求验证、受控密码重置；每个写命令自己管理事务。 |
| `api/identity.py`、`api/auth.py` | `require_principal` 依赖、认证 HTTP 契约、Cookie/Origin/CSRF 和错误映射。 |
| `api/runtime.py`、`main.py`、`config/settings.py`、`.env.example`、`requirements.txt` | 装配身份服务、收窄 CORS、配置及新依赖；保留旧接口可启动。 |
| `scripts/reset_demo_password.py` | 隔离环境的交互式受控重置；不从命令行参数读取密码。 |
| `tests/test_identity_core.py`、`tests/test_identity_mysql.py`、`tests/test_identity_rate_limit.py`、`tests/test_auth_api.py` | 单元、隔离 MySQL、隔离 Redis 和 HTTP 契约验证。 |

## Task 1：身份表、主体和密码哈希

**Files:** Create `identity/__init__.py`, `identity/models.py`, `identity/types.py`, `identity/passwords.py`, `migrations/versions/0004_identity.py`, `tests/test_identity_core.py`, `tests/test_identity_mysql.py`; modify `migrations/env.py`, `requirements.txt`.

**Interfaces:** `Principal(issuer: str, subject: str, principal_key: str)`；`UserView(id: str, email: str, email_verified: bool, source: str)`；`make_principal(issuer: str, user_id: str) -> Principal`；`hash_password(raw: str) -> str`、`verify_password(raw: str, encoded: str) -> bool`。`identity.models` 导出 `IdentityUser`、`AuthSession`、`RefreshToken`。后续任务用这些类型，不直接拼主体键。

- [ ] **Step 1: Write failing tests.** `test_password_hash_is_argon2id_and_salted` 断言同密码两次哈希不同、前缀 `$argon2id$`、验证正确/错误密码分别为真/假、存储串不含原文；`test_password_policy` 断言少于 15 字符拒绝、64 字符及含空格/Unicode 接受；`test_principal_key_is_stable_and_issuer_scoped` 断言相同 `(issuer, UUID)` 同键、不同 issuer 异键。`test_identity_migration_constraints` 用 `migrated_mysql_database` 检查三表、邮箱/令牌哈希唯一、FK、活动令牌唯一索引，`0004_identity` 可降回 `0003_inventory` 再升级；并发注册唯一性在任务 3 测。

  ```python
  first_hash, second_hash = hash_password(password), hash_password(password)
  assert first_hash.startswith("$argon2id$") and first_hash != second_hash
  assert verify_password(password, first_hash) is True
  assert make_principal("issuer-a", user_id).principal_key != make_principal("issuer-b", user_id).principal_key
  ```
- [ ] **Step 2: Run red tests.** `.venv/bin/python -m pytest tests/test_identity_core.py tests/test_identity_mysql.py -q`；新类型或迁移缺失导致失败。先设置隔离 `ECOM_TEST_DATABASE_URL`；MySQL 测试跳过不算通过。
- [ ] **Step 3: Implement model and helpers.** 增加 `argon2-cffi`；`PasswordHasher(type=Type.ID, memory_cost=19456, time_cost=2, parallelism=1)`，密码最短 15 字符且至少接受 64 字符；主体键用两段 UTF-8 长度前缀和 SHA-256。迁移 `down_revision="0003_inventory"`，InnoDB/utf8mb4、`DATETIME(6)`，活动列定义为 `CASE WHEN consumed_at IS NULL AND revoked_at IS NULL THEN auth_session_id ELSE NULL END` 的生成列并加唯一索引。模型与迁移使用一致的表/约束名。
- [ ] **Step 4: Run green checks.** 上述两个文件全量通过；`alembic upgrade head`/降级在隔离库成功，`tests/test_catalog_mysql.py` 现有迁移断言仍通过。
- [ ] **Step 5: Commit.** `git add identity migrations requirements.txt tests/test_identity_core.py tests/test_identity_mysql.py && git commit -m "feat: add identity schema and password hashing"`。

## Task 2：JWT、密钥与环境配置

**Files:** Create `identity/tokens.py`, `identity/csrf.py`; modify `identity/types.py`, `config/settings.py`, `.env.example`, `tests/test_identity_core.py`.

**Interfaces:** `TokenCodec.issue(user_id: str, session_id: str, now: datetime) -> str`；`TokenCodec.decode(token: str, now: datetime) -> AccessClaims`，`AccessClaims` 含 `user_id`, `session_id`, `jti`。`load_token_codec(settings: Settings) -> TokenCodec` 只从配置的私钥文件和 `kid -> public PEM` JSON 文件取密钥；异常为 `IdentityConfigurationError` 或 `InvalidAccessToken`，不能回显密钥内容。`CsrfSigner.issue(session_id: str) -> str`、`verify(session_id: str, value: str) -> bool` 使用单独配置密钥。

- [ ] **Step 1: Write failing tests.** `test_access_claims_and_tampering` 断言 15 分钟 TTL、所有必需字段及受控 `kid`；分别移除 `sid/iat/nbf/exp/jti`，改 `alg/iss/aud/kid`、用错误私钥签名、过期或未来 `nbf/iat` 时 `decode` 抛 `InvalidAccessToken`。`test_key_rotation_accepts_old_public_key_only_until_expiry` 用两对测试 RSA 密钥验证新旧 `kid`；`test_missing_key_config_fails_closed` 断言空路径/缺少当前 `kid` 抛配置错误。`test_csrf_is_session_bound` 断言另一会话和伪造值均验证失败。

  ```python
  assert codec.decode(codec.issue(user_id, session_id, now), now).session_id == session_id
  with pytest.raises(InvalidAccessToken):
      codec.decode(token_with_wrong_audience, now)
  assert csrf.verify(other_session_id, csrf.issue(session_id)) is False
  ```
- [ ] **Step 2: Run red test.** `.venv/bin/python -m pytest tests/test_identity_core.py -q`；新 JWT 测试先因 codec 缺失失败。
- [ ] **Step 3: Implement token codec/config.** 增加 `PyJWT[crypto]`；设置 `ECOM_IDENTITY_ENVIRONMENT=production`、`ECOM_IDENTITY_DEMO_MODE=false`、`ECOM_IDENTITY_ISSUER`、`ECOM_IDENTITY_AUDIENCE`、`ECOM_IDENTITY_PRIVATE_KEY_PATH`、`ECOM_IDENTITY_PUBLIC_KEYS_PATH`、`ECOM_IDENTITY_ACTIVE_KID`、`ECOM_IDENTITY_CSRF_SECRET`、15 分钟/30 天时效和允许来源。验签固定 `algorithms=["RS256"]`，显式要求声明，不使用令牌中的 URL。`.env.example` 仅写占位符与说明，不放密钥。
- [ ] **Step 4: Run green test.** `.venv/bin/python -m pytest tests/test_identity_core.py -q` 全部通过；旧配置缺失时不破坏现有推荐/只读服务的启动（任务 5 再作 HTTP 回归）。
- [ ] **Step 5: Commit.** `git add identity config/settings.py .env.example requirements.txt tests/test_identity_core.py && git commit -m "feat: sign and validate access tokens"`。

## Task 3：Redis 限流、演示注册与密码登录

**Files:** Create `identity/rate_limit.py`, `identity/service.py`, `tests/test_identity_rate_limit.py`; modify `identity/types.py`, `tests/test_identity_mysql.py`, `tests/conftest.py`.

**Interfaces:** `RedisRateLimiter(redis_client)` 提供异步 `check_register(client_ip: str) -> None`、`check_login(email: str, client_ip: str) -> None`、`record_login_failure(email: str, client_ip: str) -> None`、`check_refresh(session_id: str | None, client_ip: str) -> None`；超限抛 `RateLimited(retry_after_seconds)`，Redis 故障抛 `IdentityDependencyError`。`IdentityService(session_factory, token_codec: TokenCodec, limiter: RedisRateLimiter, csrf: CsrfSigner, environment: str, demo_mode: bool)` 提供异步 `register_demo(email: str, password: str, client_ip: str) -> UserView` 与 `login(email: str, password: str, client_ip: str) -> AuthIssue`。`AuthIssue` 含 `access_token`, `refresh_token`, `csrf_token`, `expires_at`, `user_id`, `session_id`，原始刷新令牌只传给设置 Cookie 的 API 层。

- [ ] **Step 1: Write failing tests.** `test_demo_register_and_login` 用真实隔离 MySQL：注册后 `password_hash != password`，用相同邮箱/密码登录取得不同于其他用户的 JWT `sub`；邮箱 NFC/大小写等价及并发注册只有一个成功、没有重复账号。`test_bad_credentials_are_indistinguishable` 比较不存在账号、错误密码、禁用账号的外部领域错误。`test_demo_mode_gate` 检查 `production + demo_mode=true` 拒绝身份服务装配，`production + demo_mode=false` 拒绝 demo 注册/登录。`test_redis_limits_and_failure` 对隔离 `ECOM_TEST_REDIS_URL` 断言规范化邮箱 5 次失败/15 分钟、IP 30 次/15 分钟、注册 3 次/小时、`Retry-After`、Redis 断连 503。

  ```python
  assert stored_user.password_hash != password
  assert codec.decode(login.access_token, now).user_id == stored_user.id
  with pytest.raises(RateLimited) as error:
      await limiter.check_register(client_ip_after_three_attempts)
  assert error.value.retry_after_seconds > 0
  ```
- [ ] **Step 2: Run red tests.** `.venv/bin/python -m pytest tests/test_identity_mysql.py tests/test_identity_rate_limit.py -q`；先配置独立 MySQL 和 Redis 测试实例。缺测试 URL 的跳过不算完成。
- [ ] **Step 3: Implement limiter and account methods.** Redis 限流用单条 Lua 脚本原子 `INCR`/首次 `EXPIRE`，按邮箱与客户端 IP 分别计数；Redis 出错拒绝。注册限于显式演示环境，校验密码长度与唯一邮箱，事务提交后返回；登录统一凭证错误，不存在账号也执行受控假哈希校验以减少时序枚举；成功时创建 `AuthSession` 和 256 位刷新令牌哈希，登录会话 30 天绝对到期，响应前提交事务。密码哈希 CPU 工作放在线程池，避免阻塞异步事件循环。
- [ ] **Step 4: Run green checks.** 两个测试文件全量通过；新 Redis/MySQL 测试没有跳过。额外运行 `.venv/bin/python -m pytest tests/test_identity_core.py -q`。
- [ ] **Step 5: Commit.** `git add identity tests/test_identity_mysql.py tests/test_identity_rate_limit.py tests/conftest.py && git commit -m "feat: register and log in demo accounts"`。

## Task 4：逐请求主体、刷新轮换、撤销与 CSRF

**Files:** Modify `identity/service.py`, `identity/types.py`, `tests/test_identity_mysql.py`, `tests/test_identity_core.py`.

**Interfaces:** `IdentityService.verify(token: str) -> Principal` 为异步方法；`IdentityService.refresh(raw_refresh: str, csrf_value: str, client_ip: str) -> AuthIssue`；`IdentityService.csrf_for_refresh(raw_refresh: str) -> str`；`IdentityService.logout(raw_refresh: str, csrf_value: str) -> None`；`IdentityService.get_me(principal: Principal) -> UserView`。上述涉及数据库的方法均为异步方法，使用任务 2 的 `CsrfSigner`。

- [ ] **Step 1: Write failing tests.** `test_verify_rechecks_user_and_session` 断言 JWT 有效但会话被撤销、过期、账号禁用或 `sub/sid` 不匹配时 401，数据库不可用时领域依赖错误。`test_refresh_rotation_and_replay` 断言旧令牌被消费、新令牌哈希落库、旧令牌重放撤销该会话；未知随机令牌不撤销别人。`test_concurrent_refresh_has_one_winner` 两个事务竞争同令牌只一次轮换，重放方触发会话撤销。`test_csrf_bound_to_session` 断言别人的/伪造值被拒，调用 `csrf_for_refresh` 不轮换令牌；`test_logout_revokes_access_immediately` 断言退出后旧 JWT 立即无效。

  ```python
  assert (await service.verify(access_token)).subject == user_id
  await service.logout(refresh_token, csrf_token)
  with pytest.raises(InvalidAccessToken):
      await service.verify(access_token)
  ```
- [ ] **Step 2: Run red tests.** `.venv/bin/python -m pytest tests/test_identity_core.py tests/test_identity_mysql.py -q`；新增用例先失败。
- [ ] **Step 3: Implement verification and session commands.** `verify` 先验 JWT，再以 `sub/sid` 查 MySQL 且校验 `source=demo` 不可在正式环境使用。刷新在固定锁顺序中锁会话和令牌行，检查消费/撤销/到期状态，事务内消费旧令牌并插入新哈希，提交后才返回新的令牌与 Cookie 值；已消费令牌重放只撤销它所属会话。`csrf_for_refresh` 先验证 Cookie 指向有效活动会话，再签发会话绑定值；`logout` 在同一会话上撤销并使后续 JWT 验证失败。
- [ ] **Step 4: Run green checks.** `.venv/bin/python -m pytest tests/test_identity_core.py tests/test_identity_mysql.py -q` 全部通过，隔离 MySQL 用例实际执行。并发竞争测试必须断言最终会话已撤销，不能只断言“一个刷新成功”。
- [ ] **Step 5: Commit.** `git add identity tests/test_identity_core.py tests/test_identity_mysql.py && git commit -m "feat: rotate and revoke identity sessions"`。

## Task 5：FastAPI 认证端点与购物主体依赖

**Files:** Create `api/auth.py`, `api/identity.py`, `tests/test_auth_api.py`; modify `api/runtime.py`, `main.py`, `config/settings.py`, `.env.example`.

**Interfaces:** `api.identity.require_principal(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False))) -> Principal`。`api.auth.router` 提供规范路径 `/api/v1/auth/register|login|csrf|refresh|logout|me`；`ApiRuntime.identity: IdentityService | None` 与 `identity_error: str | None`。未来购物路由直接 `Depends(require_principal)`，不得读取请求体 `user_id`。

- [ ] **Step 1: Write failing HTTP tests.** `test_auth_flow_and_cookie_flags` 用 HTTPS `ASGITransport`：注册不发送邮件且不自动登录；登录返回 access token 与 CSRF 值，刷新 Cookie 具有 `__Host-`/`Secure`/`HttpOnly`/`SameSite=Lax`/`Path=/` 且无 `Domain`；`/csrf` 后刷新成功，退出后旧 Bearer 请求 `/me` 为 401，`/me` 明确返回 `email_verified=false`。`test_origin_csrf_cors_and_missing_dependencies` 断言恶意 Origin、缺/错 CSRF 被拒，过期 Cookie 不能取 CSRF；只给配置的 Origin 返回 CORS 允许头且不为 `*`；缺 MySQL/密钥返回 503、不泄露秘密；带令牌而依赖故障的受保护路由为 503，无 Bearer 为 401；公开 `/api/v1/catalog/products` 或报价仍不需要身份，旧推荐返回原契约。`test_forwarded_ip_only_from_trusted_proxy` 断言非可信客户端伪造 `X-Forwarded-For` 不改变限流身份，可信代理链取最靠近服务端的非可信地址。`test_principal_dependency_isolation` 在仅测试用的保护路由中以两个真实登录主体请求同一资源，服务层按 `principal_key` 对他人资源返回 404；以后购物会话/购物车实现再复用该测试模式。

  ```python
  assert login_response.status_code == 200
  assert "HttpOnly" in login_response.headers["set-cookie"]
  assert other_user_resource_response.status_code == 404
  assert unavailable_identity_response.status_code == 503
  ```
- [ ] **Step 2: Run red test.** `.venv/bin/python -m pytest tests/test_auth_api.py -q`；新路由/依赖缺失导致失败。测试应用沿用仓库 `AsyncClient(ASGITransport(...))` 模式，并用隔离 MySQL/Redis；跳过不算完成。
- [ ] **Step 3: Wire API and runtime.** `create_runtime` 在配置完整时装配 identity、Redis、会话工厂，缺配置时只使认证/受保护端点 503，仍保留旧推荐/公开只读服务启动；`main.py` 注册 auth router。新增可信代理 CIDR 配置并在 `api/identity.py` 中实现 `resolve_client_ip(request: Request, trusted_proxy_networks: tuple[str, ...]) -> str`，仅当直接连接来源属于可信代理时解析 `X-Forwarded-For`，从右向左选择最靠近服务端的非可信地址；认证域 CORS 明确允许来源并拒绝 `*`。注册/登录/刷新/退出做 Origin 校验，刷新/退出做 CSRF；响应 `Cache-Control: no-store`，Cookie 仅由认证端点读取/写入。不要给现有公开报价全局加认证。
- [ ] **Step 4: Run green checks.** `.venv/bin/python -m pytest tests/test_auth_api.py tests/test_api_routes.py tests/test_commerce_read_api.py -q` 通过；再运行 `.venv/bin/python -m pytest tests/ -q`，MySQL/Redis 测试不得被跳过才算完成身份验收。
- [ ] **Step 5: Commit.** `git add api main.py config/settings.py .env.example tests/test_auth_api.py && git commit -m "feat: expose identity endpoints and principal dependency"`。

## Task 6：受控演示密码重置与交付验证

**Files:** Create `scripts/reset_demo_password.py`; modify `identity/service.py`, `tests/test_identity_mysql.py`, `.env.example`.

**Interfaces:** 异步 `IdentityService.reset_demo_password(user_id: str, new_password: str) -> None` 只供隔离环境的运维入口；`scripts/reset_demo_password.py --user-id <UUID>` 使用 `getpass` 读取新密码，不接受 `--password` 参数，复用同一环境门禁和密码规则。

- [ ] **Step 1: Write failing tests.** `test_controlled_reset_revokes_all_sessions` 创建同一用户两次登录，受控重置后两枚旧 JWT 均为 401、两枚刷新令牌均不能使用，新密码可登录、旧密码不可；`test_reset_denied_in_production` 断言正式环境和无效用户 ID 不修改账号；脚本调用不含密码命令行参数、不把密码或数据库 URL 输出到日志。

  ```python
  await service.reset_demo_password(user_id, new_password)
  with pytest.raises(InvalidAccessToken):
      await service.verify(old_access_token)
  assert (await service.login(email, new_password, client_ip)).user_id == user_id
  ```
- [ ] **Step 2: Run red test.** `.venv/bin/python -m pytest tests/test_identity_mysql.py -q`；新重置用例先失败。
- [ ] **Step 3: Implement reset command.** 重置由受控 CLI 调用，校验演示环境与用户来源，在同一 MySQL 事务更新 Argon2id 哈希、撤销该用户全部登录会话/刷新令牌；命令不打印秘密，失败只返回脱敏错误。
- [ ] **Step 4: Run final gates.** `.venv/bin/python -m pytest tests/test_identity_core.py tests/test_identity_mysql.py tests/test_identity_rate_limit.py tests/test_auth_api.py -q` 全通过且零跳过；`.venv/bin/python -m pytest tests/ -q` 全通过。用隔离 MySQL 验证 `0003_inventory -> 0004_identity -> 0003_inventory -> 0004_identity`；在配置缺失的本地启动中验证 `/health` 和旧推荐可用、认证端点为 503。记录真实测试结果，不用监听端口代替业务端点验证。
- [ ] **Step 5: Commit.** `git add scripts/reset_demo_password.py identity/service.py tests/test_identity_mysql.py .env.example && git commit -m "feat: reset demo passwords safely"`。

## 与后续购物计划的交接

本计划完成后，`identity/` 只保证能产生和验证主体。购物会话、购物车和交接表尚不存在；它们的实施计划须在每个 `/sessions/{id}`、车、动作、交接查询中按 `session_id + principal_key` 或相应所有权键过滤，并以真实登录取得的 token 测跨用户 404。不得将本计划 Task 5 的测试保护路由当成已完成购物授权。

## 依据

库接口选型参考 [argon2-cffi 官方文档](https://argon2-cffi.readthedocs.io/en/stable/) 与 [PyJWT 官方文档](https://pyjwt.readthedocs.io/en/stable/usage.html)；密码和会话安全边界以已审核设计稿及其引用的 OWASP 文档为准。
