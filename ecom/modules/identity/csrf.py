"""与登录会话绑定的 CSRF 签名。

刷新与退出用 Cookie 里的刷新令牌定位会话，因此必须再校验一个浏览器无法
跨站读取的值：该值是「会话 ID + 独立配置密钥」的 HMAC。换会话换值，
密钥与签名密钥分离，泄露其中之一不影响另一个。
"""

from __future__ import annotations

import hashlib
import hmac

from ecom.modules.identity.types import IdentityConfigurationError

# 独立密钥的最小长度；过短的密钥无法抵抗离线爆破。
MIN_SECRET_LENGTH = 32

_DOMAIN = b"ecom-csrf-v1\x00"


class CsrfSigner:
    """签发与校验会话绑定的 CSRF 值。"""

    def __init__(self, secret: str):
        if not isinstance(secret, str) or len(secret) < MIN_SECRET_LENGTH:
            raise IdentityConfigurationError(
                f"CSRF 签名密钥至少需要 {MIN_SECRET_LENGTH} 个字符"
            )
        self._key = secret.encode("utf-8")

    def _sign(self, session_id: str) -> str:
        message = _DOMAIN + session_id.encode("utf-8")
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def issue(self, session_id: str) -> str:
        if not session_id:
            raise ValueError("签发 CSRF 值需要非空 session_id")
        return self._sign(session_id)

    def verify(self, session_id: str, value: str) -> bool:
        """定长比较，避免通过响应时间猜值。"""
        if not session_id or not value or not isinstance(value, str):
            return False
        return hmac.compare_digest(self._sign(session_id), value)
