"""身份模块单元测试：密码哈希、主体键、访问令牌与 CSRF 原语。

这些用例不依赖 MySQL/Redis，可在任何环境执行。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from modules.identity.csrf import CsrfSigner
from modules.identity.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    hash_password,
    validate_password,
    verify_password,
)
from modules.identity.tokens import (
    ALGORITHM,
    DEFAULT_ACCESS_TTL_SECONDS,
    REQUIRED_CLAIMS,
    TokenCodec,
    load_token_codec,
)
from modules.identity.types import (
    IdentityConfigurationError,
    InvalidAccessToken,
    make_principal,
    principal_key_for,
)

ISSUER = "https://identity.ecom.test"
AUDIENCE = "ecom-api"
ACTIVE_KID = "test-key-1"
NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)


def _generate_keypair() -> tuple[str, str]:
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


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    return _generate_keypair()


@pytest.fixture(scope="module")
def other_keypair() -> tuple[str, str]:
    return _generate_keypair()


def _codec(
    keypair: tuple[str, str],
    *,
    kid: str = ACTIVE_KID,
    public_keys: dict[str, str] | None = None,
    access_ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS,
) -> TokenCodec:
    private_pem, public_pem = keypair
    return TokenCodec(
        issuer=ISSUER,
        audience=AUDIENCE,
        active_kid=kid,
        private_key_pem=private_pem,
        public_keys=public_keys if public_keys is not None else {kid: public_pem},
        access_ttl_seconds=access_ttl_seconds,
    )


def _sign(payload: dict, private_pem: str, kid: str = ACTIVE_KID) -> str:
    return jwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": kid})


def _payload(token: str) -> dict:
    """读出声明用于构造篡改用例；此处不做任何校验。"""
    return jwt.decode(
        token,
        options={
            "verify_signature": False,
            "verify_exp": False,
            "verify_nbf": False,
            "verify_iat": False,
            "verify_aud": False,
            "verify_iss": False,
        },
    )


# ---------- 密码哈希 ----------


def test_password_hash_is_argon2id_and_salted():
    """同一密码两次哈希不同（独立盐），且不回显原文。"""
    password = "correct horse battery staple"
    first_hash = hash_password(password)
    second_hash = hash_password(password)

    assert first_hash.startswith("$argon2id$")
    assert first_hash != second_hash
    # 存储串里不能出现原文片段
    assert password not in first_hash
    # 参数不低于设计下限 m=19456 KiB, t=2, p=1
    assert "m=19456,t=2,p=1" in first_hash

    assert verify_password(password, first_hash) is True
    assert verify_password(password + "!", first_hash) is False
    assert verify_password("", first_hash) is False
    assert verify_password(password, "") is False


def test_password_hash_verifies_against_hash_of_other_salt():
    """不同盐的同一密码互不干扰，验证只认自己那一份哈希。"""
    password = "an0ther-long-demo-password"
    first_hash = hash_password(password)
    second_hash = hash_password(password)

    assert verify_password(password, first_hash) is True
    assert verify_password(password, second_hash) is True
    assert verify_password("wrong-but-long-enough", first_hash) is False


def test_password_hash_rejects_corrupted_encoded_value():
    """损坏的哈希返回 False，不把库异常抛给调用方。"""
    assert verify_password("x" * 20, "not-an-argon2-hash") is False
    assert verify_password("x" * 20, "$argon2id$v=19$m=19456,t=2,p=1$broken") is False


def test_password_policy():
    """少于 15 字符拒绝；64 字符及含空格/Unicode 的密码接受。"""
    too_short = "a" * (MIN_PASSWORD_LENGTH - 1)
    with pytest.raises(PasswordPolicyError):
        validate_password(too_short)
    with pytest.raises(PasswordPolicyError):
        hash_password(too_short)

    # 边界：正好 15 个字符接受
    validate_password("a" * MIN_PASSWORD_LENGTH)

    long_ascii = "a" * 64
    validate_password(long_ascii)
    assert verify_password(long_ascii, hash_password(long_ascii)) is True

    spaced = "带空格 和 Unicode 的密码 🎉 1234"
    assert len(spaced) >= MIN_PASSWORD_LENGTH
    validate_password(spaced)
    assert verify_password(spaced, hash_password(spaced)) is True

    with pytest.raises(PasswordPolicyError):
        validate_password("a" * (MAX_PASSWORD_LENGTH + 1))
    with pytest.raises(PasswordPolicyError):
        validate_password(None)  # type: ignore[arg-type]


# ---------- 主体键 ----------


def test_principal_key_is_stable_and_issuer_scoped():
    """相同 (issuer, UUID) 同键；换 issuer 换键；编码无歧义。"""
    user_id = str(uuid.uuid4())

    assert make_principal("issuer-a", user_id) == make_principal("issuer-a", user_id)
    assert (
        make_principal("issuer-a", user_id).principal_key
        != make_principal("issuer-b", user_id).principal_key
    )

    principal = make_principal("issuer-a", user_id)
    assert principal.issuer == "issuer-a"
    assert principal.subject == user_id
    assert len(principal.principal_key) == 64
    assert principal.principal_key == principal_key_for("issuer-a", user_id)

    # 长度前缀编码：分段边界不同不能拼出同一串
    assert principal_key_for("ab", "c") != principal_key_for("a", "bc")
    # 邮箱不参与主体键：同一 issuer 下不同用户的键必不相同
    assert principal_key_for("issuer-a", "u-1") != principal_key_for("issuer-a", "u-2")


# ---------- 访问令牌 ----------


def test_access_claims_and_tampering(keypair):
    """固定 RS256、15 分钟 TTL、必需声明齐备；任一改动都被拒绝。"""
    codec = _codec(keypair)
    user_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    token = codec.issue(user_id, session_id, NOW)
    claims = codec.decode(token, NOW)
    assert claims.user_id == user_id
    assert claims.session_id == session_id

    header = jwt.get_unverified_header(token)
    assert header["alg"] == ALGORITHM
    assert header["kid"] == ACTIVE_KID

    payload = _payload(token)
    assert set(REQUIRED_CLAIMS) <= set(payload)
    assert payload["iss"] == ISSUER
    assert payload["aud"] == AUDIENCE
    assert payload["sub"] == user_id
    assert payload["sid"] == session_id
    assert payload["exp"] - payload["iat"] == DEFAULT_ACCESS_TTL_SECONDS == 900
    assert payload["nbf"] == payload["iat"]

    # 每次签发都是新的 jti
    assert codec.decode(codec.issue(user_id, session_id, NOW), NOW).jti != claims.jti

    # 缺少任一必需声明都拒绝（签名本身仍然有效）
    for claim in ("iss", "aud", "sub", "sid", "iat", "nbf", "exp", "jti"):
        broken = dict(payload)
        broken.pop(claim)
        with pytest.raises(InvalidAccessToken):
            codec.decode(_sign(broken, keypair[0]), NOW)

    # 签发方、受众被篡改
    for claim, value in (("iss", "https://evil.test"), ("aud", "other-api")):
        broken = dict(payload)
        broken[claim] = value
        with pytest.raises(InvalidAccessToken):
            codec.decode(_sign(broken, keypair[0]), NOW)

    # 空 sub / 空 sid
    for claim in ("sub", "sid"):
        broken = dict(payload)
        broken[claim] = "   "
        with pytest.raises(InvalidAccessToken):
            codec.decode(_sign(broken, keypair[0]), NOW)

    # alg=none（无签名）
    unsigned = jwt.encode(dict(payload), None, algorithm="none", headers={"kid": ACTIVE_KID})
    with pytest.raises(InvalidAccessToken):
        codec.decode(unsigned, NOW)

    # 用别的私钥签名
    with pytest.raises(InvalidAccessToken):
        codec.decode(_sign(dict(payload), _generate_keypair()[0]), NOW)

    # 时间声明：过期、nbf 在未来、iat 在未来
    expired = dict(payload, iat=int((NOW - timedelta(hours=2)).timestamp()))
    expired["exp"] = int((NOW - timedelta(minutes=30)).timestamp())
    with pytest.raises(InvalidAccessToken):
        codec.decode(_sign(expired, keypair[0]), NOW)

    for claim in ("nbf", "iat"):
        future = dict(payload)
        future[claim] = int((NOW + timedelta(minutes=10)).timestamp())
        with pytest.raises(InvalidAccessToken):
            codec.decode(_sign(future, keypair[0]), NOW)

    # 空令牌与不可解析内容
    for bad in ("", "not-a-jwt"):
        with pytest.raises(InvalidAccessToken):
            codec.decode(bad, NOW)

    # 时钟容差内的轻微偏差仍然接受
    assert codec.decode(token, NOW - timedelta(seconds=5)).session_id == session_id


def test_key_rotation_accepts_old_public_key_only_until_expiry(keypair, other_keypair):
    """轮换期间新旧 kid 都能验签；未分发旧公钥或旧令牌过期后一律拒绝。"""
    old_private, old_public = keypair
    new_private, new_public = other_keypair

    old_codec = _codec(keypair, kid="key-old", public_keys={"key-old": old_public})
    rotated = _codec(
        other_keypair,
        kid="key-new",
        public_keys={"key-new": new_public, "key-old": old_public},
    )

    user_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
    legacy_token = old_codec.issue(user_id, session_id, NOW)

    # 新配置同时信任新旧公钥：未过期的旧令牌继续可用
    assert rotated.decode(legacy_token, NOW).session_id == session_id
    # 过期后不再接受（即使旧公钥仍在集合里）
    with pytest.raises(InvalidAccessToken):
        rotated.decode(legacy_token, NOW + timedelta(hours=1))

    # 新签发使用当前 kid
    fresh = rotated.issue(user_id, session_id, NOW)
    assert jwt.get_unverified_header(fresh)["kid"] == "key-new"
    assert rotated.decode(fresh, NOW).user_id == user_id

    # 只信任新公钥的验签方拒绝旧的 kid
    new_only = _codec(other_keypair, kid="key-new", public_keys={"key-new": new_public})
    with pytest.raises(InvalidAccessToken):
        new_only.decode(legacy_token, NOW)

    # 未知 kid 一律拒绝，不按令牌自带的密钥来源验签
    unknown = _sign(_payload(fresh), new_private, kid="key-ghost")
    with pytest.raises(InvalidAccessToken):
        rotated.decode(unknown, NOW)


def test_missing_key_config_fails_closed(tmp_path, keypair):
    """密钥配置缺失或不可解析时拒绝装配，错误消息不回显密钥内容。"""
    private_pem, public_pem = keypair
    private_file = tmp_path / "identity-private.pem"
    private_file.write_text(private_pem, encoding="utf-8")
    keys_file = tmp_path / "identity-public-keys.json"
    keys_file.write_text(json.dumps({ACTIVE_KID: public_pem}), encoding="utf-8")

    from config.settings import Settings

    def settings(**overrides) -> Settings:
        base = {
            "identity_issuer": ISSUER,
            "identity_audience": AUDIENCE,
            "identity_active_kid": ACTIVE_KID,
            "identity_private_key_path": str(private_file),
            "identity_public_keys_path": str(keys_file),
            "identity_csrf_secret": "c" * 48,
        }
        base.update(overrides)
        return Settings(**base)

    # 正常配置可以签发与验签
    codec = load_token_codec(settings())
    assert codec.decode(codec.issue("u-1", "s-1", NOW), NOW).session_id == "s-1"

    for overrides in (
        {"identity_private_key_path": ""},
        {"identity_public_keys_path": ""},
        {"identity_private_key_path": str(tmp_path / "absent.pem")},
        {"identity_public_keys_path": str(tmp_path / "absent.json")},
        {"identity_active_kid": "unknown-kid"},
        {"identity_issuer": ""},
    ):
        with pytest.raises(IdentityConfigurationError) as error:
            load_token_codec(settings(**overrides))
        assert "BEGIN" not in str(error.value)

    # 公钥集合不是合法 JSON 或不是对象
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(IdentityConfigurationError):
        load_token_codec(settings(identity_public_keys_path=str(broken)))
    broken.write_text("[]", encoding="utf-8")
    with pytest.raises(IdentityConfigurationError):
        load_token_codec(settings(identity_public_keys_path=str(broken)))

    # 直接把私钥内容当路径时同样只报路径，不回显内容
    with pytest.raises(IdentityConfigurationError) as error:
        load_token_codec(settings(identity_private_key_path=private_pem))
    assert "BEGIN" not in str(error.value) or "无法读取" in str(error.value)


def test_csrf_is_session_bound():
    """CSRF 值绑定会话与密钥；换会话、伪造值、换密钥都不通过。"""
    signer = CsrfSigner("s" * 48)
    session_id = str(uuid.uuid4())
    other_session = str(uuid.uuid4())

    value = signer.issue(session_id)
    assert signer.issue(session_id) == value
    assert signer.verify(session_id, value) is True
    assert signer.verify(other_session, value) is False
    assert signer.verify(session_id, "forged-value") is False
    assert signer.verify(session_id, "") is False
    assert signer.verify("", value) is False
    assert CsrfSigner("t" * 48).verify(session_id, value) is False

    with pytest.raises(IdentityConfigurationError):
        CsrfSigner("too-short")
    with pytest.raises(ValueError):
        signer.issue("")


def test_codec_rejects_broken_or_mismatched_keys(keypair, other_keypair):
    """私钥损坏、与当前 kid 不配对或不是 RSA 时，装配阶段就失败。

    否则错误会推迟到会话事务提交之后的签发阶段，留下孤儿登录会话，
    对外还只是一个 500。
    """
    private_pem, public_pem = keypair
    other_private, other_public = other_keypair
    base = {"issuer": ISSUER, "audience": AUDIENCE, "active_kid": ACTIVE_KID}

    # 私钥与当前 kid 的公钥不配对
    with pytest.raises(IdentityConfigurationError) as mismatch:
        TokenCodec(private_key_pem=other_private, public_keys={ACTIVE_KID: public_pem}, **base)
    assert "不配对" in str(mismatch.value)

    # 私钥不可解析 / 传成公钥
    for broken in ("not-a-pem", public_pem):
        with pytest.raises(IdentityConfigurationError) as error:
            TokenCodec(private_key_pem=broken, public_keys={ACTIVE_KID: public_pem}, **base)
        assert "BEGIN" not in str(error.value) or "无法解析" in str(error.value)

    # 非 RSA 私钥：算法固定 RS256
    from cryptography.hazmat.primitives.asymmetric import ec

    ec_key = ec.generate_private_key(ec.SECP256R1())
    ec_pem = ec_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    with pytest.raises(IdentityConfigurationError):
        TokenCodec(private_key_pem=ec_pem, public_keys={ACTIVE_KID: public_pem}, **base)

    # 公钥不可解析：当前 kid 与轮换用的其他 kid 都要在装配时校验
    with pytest.raises(IdentityConfigurationError):
        TokenCodec(private_key_pem=private_pem, public_keys={ACTIVE_KID: "not-a-pem"}, **base)
    with pytest.raises(IdentityConfigurationError):
        TokenCodec(
            private_key_pem=private_pem,
            public_keys={ACTIVE_KID: public_pem, "key-old": "not-a-pem"},
            **base,
        )

    # 配对正确时照常工作；轮换用的公钥不参与配对校验
    codec = TokenCodec(
        private_key_pem=private_pem,
        public_keys={ACTIVE_KID: public_pem, "key-old": other_public},
        **base,
    )
    assert codec.decode(codec.issue("u-1", "s-1", NOW), NOW).session_id == "s-1"


def test_load_token_codec_rejects_mismatched_key_files(tmp_path, keypair, other_keypair):
    """配置文件层面的不配对同样在装配时报错，而不是等到签发。"""
    from config.settings import Settings

    private_pem, public_pem = keypair
    other_private, _other_public = other_keypair

    private_file = tmp_path / "identity-private.pem"
    private_file.write_text(other_private, encoding="utf-8")
    keys_file = tmp_path / "identity-public-keys.json"
    keys_file.write_text(json.dumps({ACTIVE_KID: public_pem}), encoding="utf-8")

    with pytest.raises(IdentityConfigurationError):
        load_token_codec(
            Settings(
                identity_issuer=ISSUER,
                identity_audience=AUDIENCE,
                identity_active_kid=ACTIVE_KID,
                identity_private_key_path=str(private_file),
                identity_public_keys_path=str(keys_file),
                identity_csrf_secret="c" * 48,
            )
        )
