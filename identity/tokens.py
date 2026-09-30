"""受控的 RS256 访问令牌：签发与验签。

安全边界：
- 算法固定 `RS256`，只按服务端配置的公钥集合验签，不接受 `alg=none`，
  也不使用令牌自带的密钥 URL（`jku`/`jwk`）。
- 头部 `kid` 必须命中配置的受控公钥；轮换时先分发新公钥再切换私钥。
- 必需声明 `iss/aud/sub/sid/iat/nbf/exp/jti` 缺一不可；异常消息不回显密钥内容。
"""

from __future__ import annotations

import json
import pathlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from identity.types import IdentityConfigurationError, InvalidAccessToken

ALGORITHM = "RS256"
REQUIRED_CLAIMS = ("iss", "aud", "sub", "sid", "iat", "nbf", "exp", "jti")

# 访问令牌默认 15 分钟；时钟容差用于容忍签发方与验签方的小幅偏差。
DEFAULT_ACCESS_TTL_SECONDS = 900
DEFAULT_CLOCK_SKEW_SECONDS = 30

# jti 随机串长度（字节），用于把同一秒内的令牌区分开。
JTI_BYTES = 16


def _as_utc(moment: datetime) -> datetime:
    """把 naive 时间按 UTC 解释，与 DATETIME(6) 列约定一致。"""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


@dataclass(frozen=True)
class AccessClaims:
    """验签通过后的访问令牌声明。"""

    user_id: str
    session_id: str
    jti: str


def _spki_der(public_key) -> bytes:
    """公钥的 DER SubjectPublicKeyInfo，用于比对两把公钥是否同一把。"""
    return public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def _load_rsa_private_key(pem: str):
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise IdentityConfigurationError(
            "身份签名私钥无法解析：应为未加密的 PEM 私钥"
        ) from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise IdentityConfigurationError(
            "身份签名私钥必须是 RSA 私钥：访问令牌算法固定 RS256"
        )
    return key


def _load_public_key(pem: str, kid: str):
    try:
        return serialization.load_pem_public_key(pem.encode("utf-8"))
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise IdentityConfigurationError(f"公钥 {kid} 无法解析：应为 PEM 公钥") from exc


class TokenCodec:
    """按固定算法与受控密钥签发/验证访问令牌。"""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        active_kid: str,
        private_key_pem: str,
        public_keys: dict[str, str],
        access_ttl_seconds: int = DEFAULT_ACCESS_TTL_SECONDS,
        clock_skew_seconds: int = DEFAULT_CLOCK_SKEW_SECONDS,
    ):
        if not issuer:
            raise IdentityConfigurationError("未配置身份签发方 issuer")
        if not audience:
            raise IdentityConfigurationError("未配置身份受众 audience")
        if not active_kid:
            raise IdentityConfigurationError("未配置当前签名密钥标识 kid")
        if active_kid not in public_keys:
            raise IdentityConfigurationError(
                "当前 kid 不在受控公钥集合中：请先分发对应公钥再切换签发密钥"
            )
        if not private_key_pem:
            raise IdentityConfigurationError("未配置身份签名私钥")
        if access_ttl_seconds <= 0:
            raise IdentityConfigurationError("访问令牌有效期必须为正数")
        if clock_skew_seconds < 0:
            raise IdentityConfigurationError("时钟容差不能为负数")

        # 装配阶段就把密钥解析并比对完：私钥损坏或与当前 kid 不配对时立即失败，
        # 否则会在会话事务提交之后、签发令牌时才炸，留下孤儿登录会话。
        private_key = _load_rsa_private_key(private_key_pem)
        active_public_key = _load_public_key(public_keys[active_kid], active_kid)
        if _spki_der(private_key.public_key()) != _spki_der(active_public_key):
            raise IdentityConfigurationError(
                f"身份签名私钥与当前 kid（{active_kid}）的公钥不配对："
                "请修正 ECOM_IDENTITY_PRIVATE_KEY_PATH 或公钥集合"
            )
        for kid, pem in public_keys.items():
            # 轮换用的其他公钥也要能解析，不能等到换 kid 时才发现配置损坏
            if kid != active_kid:
                _load_public_key(pem, kid)

        self.issuer = issuer
        self.audience = audience
        self.active_kid = active_kid
        self.access_ttl_seconds = access_ttl_seconds
        self.clock_skew_seconds = clock_skew_seconds
        self._private_key = private_key
        self._public_keys = dict(public_keys)

    def issue(self, user_id: str, session_id: str, now: datetime) -> str:
        """签发访问令牌；`now` 为 UTC（接受 naive UTC）。"""
        if not user_id or not session_id:
            raise ValueError("签发访问令牌需要非空的 user_id 与 session_id")
        issued_at = int(_as_utc(now).timestamp())
        claims = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": user_id,
            "sid": session_id,
            "iat": issued_at,
            "nbf": issued_at,
            "exp": issued_at + self.access_ttl_seconds,
            "jti": secrets.token_urlsafe(JTI_BYTES),
        }
        return jwt.encode(
            claims,
            self._private_key,
            algorithm=ALGORITHM,
            headers={"kid": self.active_kid},
        )

    def decode(self, token: str, now: datetime) -> AccessClaims:
        """验证令牌并返回声明；任何不通过都抛 `InvalidAccessToken`。"""
        if not token or not isinstance(token, str):
            raise InvalidAccessToken("缺少访问令牌")

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidAccessToken("访问令牌头部不可解析") from exc

        kid = header.get("kid")
        if not isinstance(kid, str) or kid not in self._public_keys:
            # 不按令牌自带的密钥来源验签：未知 kid 一律拒绝
            raise InvalidAccessToken("访问令牌的密钥标识不受信任")
        if header.get("alg") != ALGORITHM:
            raise InvalidAccessToken("访问令牌签名算法不受支持")

        try:
            payload = jwt.decode(
                token,
                key=self._public_keys[kid],
                algorithms=[ALGORITHM],
                issuer=self.issuer,
                audience=self.audience,
                # 库只负责签名、签发方、受众与必需声明；时间声明由下面按调用方
                # 传入的 UTC 时钟统一校验，避免验签结果依赖进程墙钟。
                options={
                    "require": list(REQUIRED_CLAIMS),
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
        except jwt.PyJWTError as exc:
            raise InvalidAccessToken("访问令牌校验失败") from exc

        moment = int(_as_utc(now).timestamp())
        skew = self.clock_skew_seconds
        for claim in ("iat", "nbf"):
            value = payload.get(claim)
            if not isinstance(value, int) or isinstance(value, bool):
                raise InvalidAccessToken(f"访问令牌缺少可用的 {claim}")
            if value > moment + skew:
                raise InvalidAccessToken(f"访问令牌的 {claim} 在未来")
        expires_at = payload.get("exp")
        if not isinstance(expires_at, int) or isinstance(expires_at, bool):
            raise InvalidAccessToken("访问令牌缺少可用的 exp")
        if expires_at <= moment - skew:
            raise InvalidAccessToken("访问令牌已过期")

        user_id = payload.get("sub")
        session_id = payload.get("sid")
        jti = payload.get("jti")
        for name, value in (("sub", user_id), ("sid", session_id), ("jti", jti)):
            if not isinstance(value, str) or not value.strip():
                raise InvalidAccessToken(f"访问令牌缺少非空 {name}")

        return AccessClaims(user_id=user_id, session_id=session_id, jti=jti)


def _read_config_text(path: str, what: str) -> str:
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        # 只报告路径与失败原因，不回显文件内容
        raise IdentityConfigurationError(f"无法读取{what}：{path}") from exc


def load_token_codec(settings) -> TokenCodec:
    """从配置的私钥文件与 `kid -> 公钥 PEM` JSON 文件构造编解码器。

    配置缺失或内容不可解析时抛 `IdentityConfigurationError`（不回显密钥内容），
    调用方应让受保护端点返回 503，而不是降级为匿名。
    """
    if not settings.identity_private_key_path:
        raise IdentityConfigurationError(
            "未配置身份签名私钥：请设置 ECOM_IDENTITY_PRIVATE_KEY_PATH"
        )
    if not settings.identity_public_keys_path:
        raise IdentityConfigurationError(
            "未配置身份公钥集合：请设置 ECOM_IDENTITY_PUBLIC_KEYS_PATH"
        )

    private_key_pem = _read_config_text(settings.identity_private_key_path, "身份签名私钥")
    raw_public_keys = _read_config_text(settings.identity_public_keys_path, "身份公钥集合")
    try:
        public_keys = json.loads(raw_public_keys)
    except json.JSONDecodeError as exc:
        raise IdentityConfigurationError(
            "身份公钥集合不是合法 JSON：应为 {\"kid\": \"-----BEGIN PUBLIC KEY-----\"}"
        ) from exc
    if not isinstance(public_keys, dict) or not public_keys:
        raise IdentityConfigurationError("身份公钥集合必须是非空 JSON 对象")
    for kid, pem in public_keys.items():
        if not isinstance(kid, str) or not isinstance(pem, str) or not pem.strip():
            raise IdentityConfigurationError("身份公钥集合的键与值都必须是字符串")

    return TokenCodec(
        issuer=settings.identity_issuer,
        audience=settings.identity_audience,
        active_kid=settings.identity_active_kid,
        private_key_pem=private_key_pem,
        public_keys=public_keys,
        access_ttl_seconds=settings.identity_access_token_ttl_seconds,
        clock_skew_seconds=settings.identity_clock_skew_seconds,
    )
