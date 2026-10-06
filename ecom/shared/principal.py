"""已验证主体与稳定的无歧义主体键编码。"""

import hashlib
import struct
from dataclasses import dataclass

PRINCIPAL_KEY_VERSION = b"ecom-principal-v1\x00"


@dataclass(frozen=True)
class Principal:
    """一次已验证请求的主体身份。

    `principal_key` 是 `(issuer, subject)` 的无歧义稳定编码摘要，
    未来接入其他身份提供方时不会与同值 `subject` 串户。
    """

    issuer: str
    subject: str
    principal_key: str


def _length_prefixed(value: str) -> bytes:
    """4 字节大端长度前缀 + UTF-8 字节，保证不同分段不会拼出同一串。"""
    raw = value.encode("utf-8")
    return struct.pack(">I", len(raw)) + raw


def principal_key_for(issuer: str, subject: str) -> str:
    digest = hashlib.sha256(
        PRINCIPAL_KEY_VERSION + _length_prefixed(issuer) + _length_prefixed(subject)
    )
    return digest.hexdigest()


def make_principal(issuer: str, user_id: str) -> Principal:
    return Principal(
        issuer=issuer, subject=user_id, principal_key=principal_key_for(issuer, user_id)
    )
