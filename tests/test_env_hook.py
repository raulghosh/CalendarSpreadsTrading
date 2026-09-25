import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "block_env_read.py"
_spec = importlib.util.spec_from_file_location("block_env_read", HOOK)
assert _spec is not None and _spec.loader is not None
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


@pytest.mark.parametrize(
    ("tool", "params"),
    [
        ("Read", {"file_path": "/repo/.env"}),
        ("Read", {"file_path": ".env.local"}),
        ("Read", {"file_path": "/repo/prod.env"}),
        ("Edit", {"file_path": "/repo/.env"}),
        ("Write", {"file_path": ".env"}),
        ("Grep", {"path": "/repo/.env", "pattern": "KEY"}),
        ("Grep", {"path": "/repo", "pattern": "KEY", "glob": ".env*"}),
        ("Bash", {"command": "cat .env"}),
        ("Bash", {"command": "grep KEY /Users/x/repo/.env"}),
        ("Bash", {"command": "python -c \"print(open('.env').read())\""}),
        ("Bash", {"command": "source .env && env"}),
        ("Bash", {"command": "cp .env.example .env"}),
        ("Bash", {"command": "head .env.production"}),
    ],
)
def test_blocks_secret_env_access(tool: str, params: dict[str, object]) -> None:
    assert hook.is_blocked(tool, params)


@pytest.mark.parametrize(
    ("tool", "params"),
    [
        ("Read", {"file_path": "/repo/.env.example"}),
        ("Read", {"file_path": "/repo/src/calscan/settings.py"}),
        ("Grep", {"path": "/repo", "pattern": "alpaca_api_key"}),
        ("Bash", {"command": "cat .env.example"}),
        ("Bash", {"command": "uv run pytest -q"}),
        ("Bash", {"command": ".venv/bin/python -c 'import os; os.environ'"}),
        ("Bash", {"command": "grep -n env_file src/calscan/settings.py"}),
    ],
)
def test_allows_everything_else(tool: str, params: dict[str, object]) -> None:
    assert not hook.is_blocked(tool, params)


def test_stdin_contract_emits_deny_json_only_when_blocked() -> None:
    def run(payload: dict[str, object]) -> str:
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    denied = json.loads(run({"tool_name": "Read", "tool_input": {"file_path": ".env"}}))
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert run({"tool_name": "Read", "tool_input": {"file_path": "README.md"}}) == ""
