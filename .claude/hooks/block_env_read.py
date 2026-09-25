#!/usr/bin/env python3
"""PreToolUse hook: stop Claude from reading or modifying .env secret files.

Templates (.env.example / .sample / .template) stay readable. For Bash this is best-effort:
it blocks any command that names a .env file, but can't see through indirection such as
shell globs (`cat .e*`) or a recursive grep over the whole tree.
"""

import json
import os
import re
import sys

SAFE_SUFFIXES = ("example", "sample", "template")
ENV_TOKEN = re.compile(
    r"\.env(?:\.(?!(?:example|sample|template)\b)[\w-]+)?(?=$|[\s'\"`;&|)<>*?,\]])"
)
REASON = (
    "Blocked by project hook (.claude/hooks/block_env_read.py): .env holds secrets and "
    "must not be read or edited by Claude. Check configuration through the app instead "
    "(e.g. whether a key is set, never its value)."
)


def is_env_file(path: str) -> bool:
    name = os.path.basename(path.rstrip("/"))
    return (
        name == ".env"
        or name.endswith(".env")
        or (name.startswith(".env.") and name[len(".env.") :] not in SAFE_SUFFIXES)
    )


def is_blocked(tool: str, params: dict[str, object]) -> bool:
    if tool == "Bash":
        return bool(ENV_TOKEN.search(str(params.get("command") or "")))
    if tool == "Grep":
        return is_env_file(str(params.get("path") or "")) or bool(
            ENV_TOKEN.search(str(params.get("glob") or ""))
        )
    path = params.get("file_path") or params.get("notebook_path") or ""
    return is_env_file(str(path))


def main() -> None:
    event = json.load(sys.stdin)
    if is_blocked(event.get("tool_name", ""), event.get("tool_input") or {}):
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": REASON,
                    }
                }
            )
        )


if __name__ == "__main__":
    main()
