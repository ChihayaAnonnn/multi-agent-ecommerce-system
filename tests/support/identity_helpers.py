"""身份 HTTP 测试共用的密钥材料与 Settings 构造。

拆分 `test_auth_api.py` 后，纯桩用例（演示页、静态路由占位）与真实
MySQL/Redis 用例都要签发令牌，键材料与 Settings 必须来自同一处，
否则两边会各自漂移出"看起来通过"的配置。
"""

from __future__ import annotations

import json

import pytest

from ecom.config import Settings

ISSUER = "https://identity.ecom.test"
AUDIENCE = "ecom-api"
KID = "test-key-1"
CSRF_SECRET = "c" * 48
ALLOWED_ORIGIN = "https://app.example.com"
TRUSTED_PROXY = "10.0.0.0/8"
DEMO_PASSWORD = "a-long-enough-demo-password"


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
