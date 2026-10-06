#!/usr/bin/env python
"""隔离环境的受控演示密码重置。

用法（在仓库根目录、已激活虚拟环境）：

    .venv/bin/python tools/reset_demo_password.py --user-id <UUID>

密码通过交互式输入读取，**不接受命令行密码参数**，也不会打印密码、数据库 URL
或任何令牌。重置会同时撤销该账号的全部登录会话，旧 access/refresh token 立即失效。

演示版没有自助找回密码：这是唯一的重置路径，且只在 development/test/demo 环境有效。
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import pathlib
import sys
from collections.abc import Sequence

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ecom.api.deps import build_identity_service  # noqa: E402
from ecom.config import get_settings  # noqa: E402
from ecom.infra.db import create_session_factory  # noqa: E402
from ecom.modules.identity.passwords import PasswordPolicyError  # noqa: E402
from ecom.modules.identity.types import IdentityError  # noqa: E402

PROMPT = "新密码（至少 15 个字符）："
PROMPT_AGAIN = "再次输入新密码："


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="重置隔离环境里的演示账号密码，并撤销该账号全部登录会话。",
    )
    parser.add_argument("--user-id", required=True, help="演示账号的 UUID（identity_users.id）")
    return parser.parse_args(argv)


def prompt_new_password() -> str:
    """交互式读取并二次确认；两次不一致按取消处理。"""
    first = getpass.getpass(PROMPT)
    second = getpass.getpass(PROMPT_AGAIN)
    if first != second:
        raise ValueError("两次输入不一致")
    return first


async def reset_demo_password(user_id: str, new_password: str) -> None:
    """按应用配置装配身份服务并执行重置。"""
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    from redis.asyncio import Redis

    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        service = build_identity_service(settings, session_factory, redis_client)
        await service.reset_demo_password(user_id, new_password)
    finally:
        await redis_client.aclose()


def main(argv: Sequence[str] | None = None, *, password_reader=None) -> int:
    args = parse_args(argv)
    reader = password_reader or prompt_new_password

    try:
        new_password = reader()
    except (ValueError, EOFError, KeyboardInterrupt) as exc:
        # 只回报失败原因，不回显输入内容
        print(f"已取消：{exc or '未提供密码'}", file=sys.stderr)
        return 1

    try:
        asyncio.run(reset_demo_password(args.user_id, new_password))
    except (IdentityError, PasswordPolicyError, ValueError) as exc:
        print(f"重置失败：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # 依赖故障等：只报类型，避免把连接串带进日志
        print(f"重置失败：{type(exc).__name__}", file=sys.stderr)
        return 1

    print(f"已重置演示账号 {args.user_id} 的密码，并撤销其全部登录会话。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
