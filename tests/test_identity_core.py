"""身份模块单元测试：密码哈希、主体键与后续任务的令牌原语。

这些用例不依赖 MySQL/Redis，可在任何环境执行。
"""

from __future__ import annotations

import uuid

import pytest

from identity.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    hash_password,
    validate_password,
    verify_password,
)
from identity.types import make_principal, principal_key_for


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
