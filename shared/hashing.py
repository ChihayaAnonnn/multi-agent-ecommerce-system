"""规范化请求哈希，不包含凭证。"""

import hashlib
import json
from typing import Any

JsonObject = dict[str, Any]


def request_hash(data: JsonObject) -> str:
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, ensure_ascii=False, separators=(',', ':')
        ).encode()
    ).hexdigest()
