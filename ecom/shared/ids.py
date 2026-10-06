"""应用主键生成。"""

import uuid


def new_id() -> str:
    """应用生成的 UUID 主键。"""
    return str(uuid.uuid4())
