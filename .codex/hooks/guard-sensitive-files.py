#!/usr/bin/env python3
"""Block edits to local credential files from Codex hook JSON input."""

import json
import os
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    tool_input = payload.get("tool_input") or {}
    raw_path = tool_input.get("file_path")
    if not raw_path:
        return 0

    cwd = Path(payload.get("cwd") or os.getcwd()).resolve()
    file_path = Path(raw_path)
    if not file_path.is_absolute():
        file_path = cwd / file_path
    file_path = file_path.resolve()

    if file_path.name == "imap.json" or file_path.name == ".env":
        print(f"BLOCK: Cannot edit credential file {file_path}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
