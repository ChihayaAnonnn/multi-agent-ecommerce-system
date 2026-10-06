"""Argon2id 密码哈希与演示版密码策略。

只保存哈希：明文密码不落库、不入日志、不进异常消息。参数取 OWASP 建议下限
（m=19456 KiB, t=2, p=1），每个密码由库自行生成独立盐。
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

from ecom.shared.errors import DomainError

# 演示密码策略：只要长度，不强制字符组合，允许空格与 Unicode。
MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 128

# 与迁移/文档保持一致的固定参数，便于日后按需要重新哈希。
ARGON2_MEMORY_COST_KIB = 19456
ARGON2_TIME_COST = 2
ARGON2_PARALLELISM = 1

PASSWORD_HASHER = PasswordHasher(
    type=Type.ID,
    memory_cost=ARGON2_MEMORY_COST_KIB,
    time_cost=ARGON2_TIME_COST,
    parallelism=ARGON2_PARALLELISM,
)


class PasswordPolicyError(ValueError, DomainError):
    """密码不符合演示版策略；消息只说明长度要求，不回显密码。"""


def validate_password(raw: str) -> None:
    """校验密码策略；不合规时抛 `PasswordPolicyError`。"""
    if not isinstance(raw, str):
        raise PasswordPolicyError("密码必须是字符串")
    length = len(raw)
    if length < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"密码至少需要 {MIN_PASSWORD_LENGTH} 个字符")
    if length > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"密码最多 {MAX_PASSWORD_LENGTH} 个字符")


def hash_password(raw: str) -> str:
    """返回 Argon2id 编码串（含算法、参数与随机盐）。"""
    validate_password(raw)
    return PASSWORD_HASHER.hash(raw)


def verify_password(raw: str, encoded: str) -> bool:
    """校验密码；哈希损坏或参数不识别时返回 False，不抛库异常。"""
    if not raw or not encoded:
        return False
    try:
        return PASSWORD_HASHER.verify(encoded, raw)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
