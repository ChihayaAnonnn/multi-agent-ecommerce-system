"""共享原语迁移不得改变身份、时间、幂等摘要与 ORM metadata。"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import TypeAdapter

from tests.test_layering_baseline import MODEL_MODULES


def test_base_alias_has_single_metadata():
    import importlib

    from db.base import Base as old_base
    from db.base import TimestampMixin as old_mixin
    from shared.db import Base, TimestampMixin

    assert old_base is Base
    assert old_mixin is TimestampMixin
    assert old_base.metadata is Base.metadata
    for name in MODEL_MODULES:
        module = importlib.import_module(f"{name}.models")
        models = [
            value for value in vars(module).values()
            if isinstance(value, type) and hasattr(value, "__table__")
        ]
        assert models
        assert all(model.metadata is Base.metadata for model in models)


@pytest.mark.parametrize(("issuer", "subject", "expected"), [
    ("issuer-a", "u-1",
     "62106752c3e96e0f6d49a7444cee3f464c9d1c292c9ea387be38459dacee2e91"),
    ("发行方", "用户甲",
     "7762b0c7afc0d1e4489fc2630c96f33837304c028b16b793aaabd14a56bba45b"),
    ("ab", "c",
     "67dae6ce0789132ca1065f359089af6753470afce4976ffc82b276587d63d868"),
    ("a", "bc",
     "f8e02bff910a4aa5c66d78004a7deeeb59cc6290ca9b2e3006db427c66d42f8f"),
    ("", "",
     "4e7c740c308df7eeae89e12635162c3caaf03c47118a0d682ce7319a660b13ca"),
])
def test_principal_key_is_unchanged(issuer, subject, expected):
    from identity import types as old
    from shared.principal import Principal, make_principal, principal_key_for

    assert old.Principal is Principal
    assert old.make_principal is make_principal
    assert old.principal_key_for is principal_key_for
    assert principal_key_for(issuer=issuer, subject=subject) == expected
    assert make_principal(issuer=issuer, user_id=subject) == Principal(
        issuer=issuer, subject=subject, principal_key=expected
    )


def test_unicode_hash_and_order_are_unchanged():
    from commerce_orchestrator.utils import request_hash as old_hash
    from shared.hashing import request_hash

    data = {
        "商品": "咖啡☕", "quantity": 2, "items": ["甲", "乙"],
        "price": 12.5, "optional": None, "approved": False,
    }
    assert old_hash is request_hash
    assert request_hash({}) == (
        "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    )
    assert request_hash(data) == (
        "5e2bc5add437e17cf73af795920a3f48aaba7fa0ae18900a6438c1d2072d42d8"
    )
    assert request_hash(dict(reversed(list(data.items())))) == request_hash(data)
    assert request_hash({**data, "items": ["乙", "甲"]}) == (
        "e1e3b4ca01a0e2168c7ae9cc782fd68711d0340fc329c2743eec7a336b65b556"
    )


def test_utc_round_trip_is_unchanged():
    from db import base as old
    from shared.time import UtcDateTime, mark_utc, utcnow

    assert old.UtcDateTime is UtcDateTime
    assert old._mark_utc is mark_utc
    assert old.utcnow is utcnow
    naive = datetime(2026, 10, 6, 1, 2, 3, 456789)
    aware = naive.replace(tzinfo=timezone(timedelta(hours=8)))
    adapter = TypeAdapter(UtcDateTime)
    assert adapter.dump_json(adapter.validate_python(naive)) == (
        b'"2026-10-06T01:02:03.456789Z"'
    )
    assert adapter.validate_json(b'"2026-10-06T01:02:03.456789Z"') == (
        naive.replace(tzinfo=timezone.utc)
    )
    assert mark_utc(aware) is aware
    assert adapter.dump_json(adapter.validate_python(aware)) == (
        b'"2026-10-06T01:02:03.456789+08:00"'
    )
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    now = utcnow()
    after = datetime.now(timezone.utc).replace(tzinfo=None)
    assert now.tzinfo is None
    assert before <= now <= after


def test_new_id_remains_uuid4_and_same_alias():
    from db.base import new_id as old_id
    from shared.ids import new_id

    assert old_id is new_id
    generated = [new_id() for _ in range(2)]
    assert generated[0] != generated[1]
    assert all(UUID(value).version == 4 for value in generated)


def test_domain_error_is_a_fieldless_exception_base():
    from shared.errors import DomainError

    error = DomainError("示例错误")
    assert isinstance(error, Exception)
    assert str(error) == "示例错误"
    assert vars(error) == {}
