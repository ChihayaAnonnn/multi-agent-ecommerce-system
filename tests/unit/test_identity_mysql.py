"""受控密码重置脚本的纯桩用例：不连数据库、不回显秘密。"""

from __future__ import annotations

import pathlib

import pytest

from ecom.modules.identity.types import IdentityConfigurationError

TOOLS_DIR = pathlib.Path(__file__).resolve().parents[2] / "tools"


def _load_reset_script():
    import importlib.util

    path = TOOLS_DIR / "reset_demo_password.py"
    spec = importlib.util.spec_from_file_location("reset_demo_password", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reset_script_takes_no_password_argument_and_hides_secrets(monkeypatch, capsys):
    """脚本只从交互式输入读密码，失败时不回显密码或数据库 URL。"""
    secret = "a-very-secret-demo-password"
    script = _load_reset_script()

    # 不接受命令行密码参数（argparse 会把参数原样回显，这里用占位值）
    with pytest.raises(SystemExit):
        script.parse_args(
            ["--user-id", "00000000-0000-0000-0000-000000000000", "--password", "placeholder"]
        )
    parsed = script.parse_args(["--user-id", "00000000-0000-0000-0000-000000000000"])
    assert parsed.user_id == "00000000-0000-0000-0000-000000000000"
    capsys.readouterr()

    # 交互式读取：源码里必须用 getpass，而不是明文回显
    source = (TOOLS_DIR / "reset_demo_password.py").read_text(encoding="utf-8")
    assert "getpass" in source
    assert "--password" not in source

    # 失败路径只输出脱敏原因
    async def failing_reset(user_id: str, new_password: str) -> None:
        raise IdentityConfigurationError(
            "未配置身份签名私钥：请设置 ECOM_IDENTITY_PRIVATE_KEY_PATH"
        )

    monkeypatch.setattr(script, "reset_demo_password", failing_reset)
    exit_code = script.main(
        ["--user-id", "00000000-0000-0000-0000-000000000000"],
        password_reader=lambda: secret,
    )
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert exit_code == 1
    assert secret not in output
    assert "mysql+asyncmy://" not in output
    assert "ECOM_IDENTITY_PRIVATE_KEY_PATH" in output

    # 交互式读取：两次不一致按取消处理，一致才返回
    class StubGetpass:
        def __init__(self, answers):
            self.answers = list(answers)

        def getpass(self, prompt=""):
            return self.answers.pop(0)

    monkeypatch.setattr(script, "getpass", StubGetpass([secret, "different-long-password"]))
    with pytest.raises(ValueError):
        script.prompt_new_password()
    monkeypatch.setattr(script, "getpass", StubGetpass([secret, secret]))
    assert script.prompt_new_password() == secret

    # 取消时不执行重置，也不回显密码
    def cancelling_reader() -> str:
        raise ValueError("两次输入不一致")

    assert (
        script.main(
            ["--user-id", "00000000-0000-0000-0000-000000000000"],
            password_reader=cancelling_reader,
        )
        == 1
    )
    assert secret not in capsys.readouterr().err
