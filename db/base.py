"""共享原语的兼容入口；定义只存在于 shared。"""

from shared.db import DATETIME6 as DATETIME6
from shared.db import NAMING_CONVENTION as NAMING_CONVENTION
from shared.db import Base as Base
from shared.db import TimestampMixin as TimestampMixin
from shared.ids import new_id as new_id
from shared.time import UtcDateTime as UtcDateTime
from shared.time import mark_utc as _mark_utc
from shared.time import utcnow as utcnow

__all__ = [
    "DATETIME6", "NAMING_CONVENTION", "Base", "TimestampMixin", "new_id",
    "UtcDateTime", "_mark_utc", "utcnow",
]
