#!/usr/bin/env python3
"""Run ruff formatting/checks for Python files after Codex writes."""

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path("/Users/yaelmeya/git/m0sh1.cc/apple-mail-mcp")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_input = payload.get("tool_input") or {}
    raw_path = tool_input.get("file_path")
    if not raw_path:
        return 0

    cwd = Path(payload.get("cwd") or PROJECT_ROOT).resolve()
    file_path = Path(raw_path)
    if not file_path.is_absolute():
        file_path = cwd / file_path
    file_path = file_path.resolve()

    if file_path.suffix != ".py":
        return 0

    try:
        file_path.relative_to(PROJECT_ROOT)
    except ValueError:
        return 0

    if not file_path.exists():
        return 0

    commands = (
        ("uv", "run", "ruff", "format", "--quiet", str(file_path)),
        ("uv", "run", "ruff", "check", "--fix", "--quiet", str(file_path)),
    )
    for command in commands:
        result = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
            return result.returncode

    return 0


if __name__ == "__main__":
    sys.exit(main())
