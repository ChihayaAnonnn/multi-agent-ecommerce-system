"""认证端点的 HTTP 契约：Cookie 属性、CSRF、Origin、CORS、依赖故障与主体隔离。

全部走真实的 MySQL 与 Redis，并用 HTTPS 传输（`__Host-` 与 `Secure` 生效的前提）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from api.auth import router as auth_router
from api.catalog import router as catalog_router
from api.commerce_read import router as commerce_read_router
from api.health import router as health_router
from api.identity import configure_cors, require_principal
from api.recommendations import router as recommendations_router
from api.runtime import build_identity_service
from catalog.service import CatalogService
from config.settings import Settings
from db.session import create_session_factory
from identity.types import IdentityHttpConfig, Principal, make_principal
from inventory.service import InventoryService
from models.schemas import Product, RecommendationResponse
from pricing.service import PricingService
from services.ab_test import ABTestEngine
from services.metrics import MetricsCollector

ISSUER = "https://identity.ecom.test"
AUDIENCE = "ecom-api"
KID = "test-key-1"
CSRF_SECRET = "c" * 48
ALLOWED_ORIGIN = "https://app.example.com"
TRUSTED_PROXY = "10.0.0.0/8"
DEMO_PASSWORD = "a-long-enough-demo-password"

BASE_URL = "https://test"


# ---------- 测试替身与夹具 ----------


class StubSupervisor:
    """旧推荐接口的替身：只验证契约不变，不触发 LLM。"""

    async def recommend(self, request):
        return RecommendationResponse(
            request_id="req-auth",
            user_id=request.user_id,
            products=[Product(product_id="P001", name="商品", category="配件", price=99)],
            marketing_copies=[{"product_id": "P001", "copy": "商品说明"}],
            experiment_group="control",
            agent_results={},
            total_latency_ms=1.0,
        )


def generate_keypair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return private_pem, public_pem


@pytest.fixture(scope="session")
def identity_key_files(tmp_path_factory) -> tuple[str, str]:
    directory = tmp_path_factory.mktemp("identity-keys")
    private_pem, public_pem = generate_keypair()
    private_file = directory / "identity-private.pem"
    private_file.write_text(private_pem, encoding="utf-8")
    keys_file = directory / "identity-public-keys.json"
    keys_file.write_text(json.dumps({KID: public_pem}), encoding="utf-8")
    return str(private_file), str(keys_file)


def identity_settings(identity_key_files, **overrides) -> Settings:
    private_path, keys_path = identity_key_files
    base = {
        "identity_environment": "test",
        "identity_demo_mode": True,
        "identity_issuer": ISSUER,
        "identity_audience": AUDIENCE,
        "identity_active_kid": KID,
        "identity_private_key_path": private_path,
        "identity_public_keys_path": keys_path,
        "identity_csrf_secret": CSRF_SECRET,
        "identity_allowed_origins": ALLOWED_ORIGIN,
        "identity_trusted_proxy_networks": TRUSTED_PROXY,
    }
    base.update(overrides)
    return Settings(**base)


# ---------- 仅测试用的受保护路由 ----------

PROTECTED_RESOURCES: dict[str, str] = {}

protected_router = APIRouter()


@protected_router.get("/api/v1/test/owned-resources/{resource_id}")
async def read_owned_resource(
    resource_id: str, principal: Principal = Depends(require_principal)
):
    """按 `principal_key` 归属过滤：他人的资源一律 404。

    仅用于验证主体依赖，不代表购物会话/购物车授权已经实现。
    """
    if PROTECTED_RESOURCES.get(resource_id) != principal.principal_key:
        raise HTTPException(status_code=404, detail="资源不存在")
    return {"resource_id": resource_id, "subject": principal.subject}


@protected_router.get("/api/v1/test/client-ip")
async def echo_client_ip(request: Request):
    from api.identity import client_ip_for

    return {"client_ip": client_ip_for(request)}


def build_app(runtime, settings: Settings) -> FastAPI:
    app = FastAPI()
    configure_cors(app, settings)
    for router in (
        health_router,
        recommendations_router,
        catalog_router,
        commerce_read_router,
        auth_router,
        protected_router,
    ):
        app.include_router(router)
    app.state.runtime = runtime
    return app


def build_runtime(database_url: str, redis_url: str, settings: Settings, *, client) -> SimpleNamespace:
    """在事件循环内装配运行时；Redis 连接属于当前循环。"""
    factory = create_session_factory(database_url)
    pricing = PricingService(factory)
    redis_client = client or Redis.from_url(redis_url, decode_responses=True)
    return SimpleNamespace(
        ab_engine=ABTestEngine(),
        metrics_collector=MetricsCollector(),
        supervisor=StubSupervisor(),
        rec_graph=None,
        catalog=CatalogService(factory, price_reader=pricing),
        pricing=pricing,
        inventory=InventoryService(factory),
        catalog_error=None,
        identity=build_identity_service(settings, factory, redis_client),
        identity_config=IdentityHttpConfig(
            allowed_origins=settings.allowed_origins,
            trusted_proxy_networks=settings.trusted_proxy_networks,
            session_ttl_seconds=settings.identity_session_ttl_seconds,
        ),
        identity_error=None,
        redis=redis_client,
    )


def http_client(app, *, client=("198.51.100.7", 1234)) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, client=client), base_url=BASE_URL
    )  # noqa: E501


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient, email: str, password: str = DEMO_PASSWORD):
    return await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )


async def _login(client: AsyncClient, email: str, password: str = DEMO_PASSWORD):
    return await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )


# ---------- 注册、登录、刷新、退出 ----------


def test_auth_flow_and_cookie_flags(migrated_mysql_database: str, clean_redis: str, identity_key_files):
    """完整认证链路与 Cookie 属性；注册不发邮件也不自动登录。"""
    settings = identity_settings(identity_key_files)

    async def scenario():
        runtime = build_runtime(migrated_mysql_database, clean_redis, settings, client=None)
        app = build_app(runtime, settings)
        async with http_client(app) as client:
            register = await _register(client, "Flow@Example.com")
            assert register.status_code == 201, register.text
            body = register.json()
            assert body["email"] == "flow@example.com"
            assert body["email_verified"] is False
            assert body["source"] == "demo"
            # 注册不自动登录：没有下发刷新 Cookie
            assert "set-cookie" not in register.headers
            assert (await client.get("/api/v1/auth/me")).status_code == 401

            login = await _login(client, "flow@example.com")
            assert login.status_code == 200, login.text
            assert login.headers["cache-control"] == "no-store"
            token = login.json()
            assert token["token_type"] == "Bearer"
            assert token["csrf_token"]
            assert token["session_id"]

            cookie = login.headers["set-cookie"]
            assert cookie.startswith(f"{runtime.identity_config.refresh_cookie_name}=")
            assert "HttpOnly" in cookie
            assert "Secure" in cookie
            assert "Path=/" in cookie
            assert "SameSite=lax" in cookie
            assert "Domain=" not in cookie

            me = await client.get("/api/v1/auth/me", headers=bearer(token["access_token"]))
            assert me.status_code == 200
            assert me.json() == {
                "user_id": token["user_id"],
                "email": "flow@example.com",
                "email_verified": False,
                "source": "demo",
            }
            assert me.headers["cache-control"] == "no-store"

            csrf_response = await client.get("/api/v1/auth/csrf")
            assert csrf_response.status_code == 200
            csrf = csrf_response.json()["csrf_token"]
            assert csrf == token["csrf_token"]

            refreshed = await client.post(
                "/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf}
            )
            assert refreshed.status_code == 200, refreshed.text
            rotated = refreshed.json()
            assert rotated["access_token"] != token["access_token"]
            assert rotated["session_id"] == token["session_id"]
            # 刷新轮换了 Cookie
            assert refreshed.headers["set-cookie"] != cookie
            assert (
                await client.get(
                    "/api/v1/auth/me", headers=bearer(rotated["access_token"])
                )
            ).status_code == 200

            logout = await client.post(
                "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}
            )
            assert logout.status_code == 204
            assert "Max-Age=0" in logout.headers["set-cookie"] or "max-age=0" in (
                logout.headers["set-cookie"]
            )

            # 退出后旧 Bearer 立即失效
            after_logout = await client.get(
                "/api/v1/auth/me", headers=bearer(rotated["access_token"])
            )
            assert after_logout.status_code == 401
            assert (await client.get("/api/v1/auth/csrf")).status_code == 401

        await runtime.redis.aclose()

    asyncio.run(scenario())


def test_origin_csrf_cors_and_missing_dependencies(
    migrated_mysql_database: str, clean_redis: str, identity_key_files
):
    """恶意 Origin、缺/错 CSRF、CORS 白名单与身份依赖故障的处理。"""
    settings = identity_settings(identity_key_files)

    async def scenario():
        runtime = build_runtime(migrated_mysql_database, clean_redis, settings, client=None)
        app = build_app(runtime, settings)
        async with http_client(app) as client:
            await _register(client, "guard@example.com")

            # 恶意 Origin 的写操作被拒
            for path, payload in (
                ("/api/v1/auth/login", {"email": "guard@example.com", "password": DEMO_PASSWORD}),
                ("/api/v1/auth/register", {"email": "new@example.com", "password": DEMO_PASSWORD}),
            ):
                rejected = await client.post(
                    path, json=payload, headers={"Origin": "https://evil.test"}
                )
                assert rejected.status_code == 403, path

            # 预检：只给配置的来源放行，且绝不为 *
            preflight = await client.options(
                "/api/v1/auth/login",
                headers={
                    "Origin": ALLOWED_ORIGIN,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "X-CSRF-Token",
                },
            )
            assert preflight.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
            assert "*" not in preflight.headers["access-control-allow-origin"]
            denied = await client.options(
                "/api/v1/auth/login",
                headers={
                    "Origin": "https://evil.test",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert "access-control-allow-origin" not in denied.headers

            # 刷新/退出缺 CSRF 或 CSRF 不匹配都被拒
            login = await _login(client, "guard@example.com")
            assert login.status_code == 200
            csrf = login.json()["csrf_token"]
            assert (await client.post("/api/v1/auth/refresh")).status_code == 403
            assert (
                await client.post(
                    "/api/v1/auth/refresh", headers={"X-CSRF-Token": "forged"}
                )
            ).status_code == 403
            assert (await client.post("/api/v1/auth/logout")).status_code == 403

            # Cookie 不可用时取不到 CSRF
            client.cookies.set(runtime.identity_config.refresh_cookie_name, "garbage")
            assert (await client.get("/api/v1/auth/csrf")).status_code == 401
            client.cookies.delete(runtime.identity_config.refresh_cookie_name)
            assert (await client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf})).status_code == 401

        # 身份未装配：认证端点 503，且不泄露密钥内容
        broken = SimpleNamespace(
            ab_engine=ABTestEngine(),
            metrics_collector=MetricsCollector(),
            supervisor=StubSupervisor(),
            rec_graph=None,
            catalog=None,
            pricing=None,
            inventory=None,
            catalog_error=None,
            identity=None,
            identity_config=runtime.identity_config,
            identity_error="未配置身份签名私钥：请设置 ECOM_IDENTITY_PRIVATE_KEY_PATH",
            redis=None,
        )
        broken_app = build_app(broken, settings)
        async with http_client(broken_app) as client:
            unavailable = await _login(client, "guard@example.com")
            assert unavailable.status_code == 503
            detail = unavailable.json()["detail"]
            assert "BEGIN" not in detail and "PRIVATE KEY" not in detail
            assert (await client.get("/api/v1/auth/csrf")).status_code == 503

            # 受保护路由：无 Bearer 是 401，带令牌而依赖故障是 503
            assert (await client.get("/api/v1/test/owned-resources/res-1")).status_code == 401
            assert (
                await client.get(
                    "/api/v1/test/owned-resources/res-1", headers=bearer("any-token")
                )
            ).status_code == 503

            # 旧推荐接口不受身份配置影响
            recommendation = await client.post(
                "/api/v1/recommend", json={"user_id": "u-1", "num_items": 1}
            )
            assert recommendation.status_code == 200
            assert recommendation.json()["request_id"] == "req-auth"

        # 公开只读接口不需要身份
        async with http_client(app) as client:
            listing = await client.get("/api/v1/catalog/products")
            assert listing.status_code == 200
            assert (await client.get("/api/v1/auth/me")).status_code == 401

        await runtime.redis.aclose()

    asyncio.run(scenario())


def test_forwarded_ip_only_from_trusted_proxy(
    migrated_mysql_database: str, clean_redis: str, identity_key_files
):
    """伪造 X-Forwarded-For 不改变限流身份；可信代理链取最靠近服务端的非可信地址。"""
    settings = identity_settings(identity_key_files)

    async def scenario():
        runtime = build_runtime(migrated_mysql_database, clean_redis, settings, client=None)
        app = build_app(runtime, settings)

        # 非可信客户端：伪造的 X-Forwarded-For 完全无效
        async with http_client(app, client=("198.51.100.7", 1234)) as untrusted:
            forged = await untrusted.get(
                "/api/v1/test/client-ip", headers={"X-Forwarded-For": "1.2.3.4"}
            )
            assert forged.json()["client_ip"] == "198.51.100.7"

            # 限流身份仍是直连地址：换伪造值不能获得新的注册配额
            for index in range(3):
                created = await _register(untrusted, f"spoof{index}@example.com")
                assert created.status_code == 201, created.text
            blocked = await untrusted.post(
                "/api/v1/auth/register",
                json={"email": "spoof-again@example.com", "password": DEMO_PASSWORD},
                headers={"X-Forwarded-For": "198.51.100.99"},
            )
            assert blocked.status_code == 429
            assert int(blocked.headers["retry-after"]) > 0

        # 可信代理：从右向左取最靠近服务端的非可信地址
        async with http_client(app, client=("10.0.0.5", 4321)) as trusted:
            chain = await trusted.get(
                "/api/v1/test/client-ip",
                headers={"X-Forwarded-For": "1.2.3.4, 10.0.0.9"},
            )
            assert chain.json()["client_ip"] == "1.2.3.4"

            all_trusted = await trusted.get(
                "/api/v1/test/client-ip",
                headers={"X-Forwarded-For": "10.0.0.9, 10.0.0.11"},
            )
            assert all_trusted.json()["client_ip"] == "10.0.0.5"

            malformed = await trusted.get(
                "/api/v1/test/client-ip", headers={"X-Forwarded-For": "not-an-ip"}
            )
            assert malformed.json()["client_ip"] == "10.0.0.5"

            no_header = await trusted.get("/api/v1/test/client-ip")
            assert no_header.json()["client_ip"] == "10.0.0.5"

            # 代理链路解析出的真实客户端拥有独立配额
            through_proxy = await trusted.post(
                "/api/v1/auth/register",
                json={"email": "via-proxy@example.com", "password": DEMO_PASSWORD},
                headers={"X-Forwarded-For": "1.2.3.4, 10.0.0.9"},
            )
            assert through_proxy.status_code == 201, through_proxy.text

        await runtime.redis.aclose()

    asyncio.run(scenario())


def test_principal_dependency_isolation(
    migrated_mysql_database: str, clean_redis: str, identity_key_files
):
    """两个真实登录主体请求同一资源，服务层按 principal_key 对他人返回 404。"""
    settings = identity_settings(identity_key_files)
    PROTECTED_RESOURCES.clear()

    async def scenario():
        runtime = build_runtime(migrated_mysql_database, clean_redis, settings, client=None)
        app = build_app(runtime, settings)
        async with http_client(app) as client:
            await _register(client, "owner@example.com")
            await _register(client, "intruder@example.com")
            owner_login = await _login(client, "owner@example.com")
            intruder_login = await _login(client, "intruder@example.com")
            owner_token = owner_login.json()["access_token"]
            intruder_token = intruder_login.json()["access_token"]

            owner_id = owner_login.json()["user_id"]
            intruder_id = intruder_login.json()["user_id"]
            assert owner_id != intruder_id
            PROTECTED_RESOURCES["res-owner"] = make_principal(ISSUER, owner_id).principal_key

            allowed = await client.get(
                "/api/v1/test/owned-resources/res-owner", headers=bearer(owner_token)
            )
            assert allowed.status_code == 200
            assert allowed.json()["subject"] == owner_id

            # 他人资源是 404（不泄露其是否存在），未知资源同样是 404
            denied = await client.get(
                "/api/v1/test/owned-resources/res-owner", headers=bearer(intruder_token)
            )
            assert denied.status_code == 404
            missing = await client.get(
                "/api/v1/test/owned-resources/res-ghost", headers=bearer(owner_token)
            )
            assert missing.status_code == 404

            # 主体键只由 (issuer, subject) 决定，不含邮箱
            assert make_principal(ISSUER, owner_id).principal_key == PROTECTED_RESOURCES["res-owner"]
            assert (
                make_principal("https://other-issuer.test", owner_id).principal_key
                != PROTECTED_RESOURCES["res-owner"]
            )

        PROTECTED_RESOURCES.clear()
        await runtime.redis.aclose()

    asyncio.run(scenario())
