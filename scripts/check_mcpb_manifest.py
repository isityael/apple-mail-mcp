#!/usr/bin/env python3
"""Validate MCPB manifest tool metadata against the runtime MCP server."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _manifest_tool_names(repo_root: Path) -> list[str]:
    manifest_path = repo_root / "apple-mail-mcpb" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    tools = manifest.get("tools")
    if not isinstance(tools, list):
        raise ValueError("apple-mail-mcpb/manifest.json field 'tools' must be a list")

    names: list[str] = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            raise ValueError(f"manifest tool at index {index} must contain a string name")
        names.append(tool["name"])
    return names


def _runtime_tool_names(repo_root: Path) -> list[str]:
    sys.path.insert(0, str(repo_root))
    from apple_mail_mcp import mcp

    return sorted(mcp._tool_manager._tools)


def check(repo_root: Path | None = None) -> tuple[bool, list[str]]:
    if repo_root is None:
        repo_root = _repo_root()

    manifest_names = _manifest_tool_names(repo_root)
    runtime_names = _runtime_tool_names(repo_root)
    errors: list[str] = []

    duplicates = sorted({name for name in manifest_names if manifest_names.count(name) > 1})
    if duplicates:
        errors.append(f"duplicate manifest tools: {', '.join(duplicates)}")

    manifest_set = set(manifest_names)
    runtime_set = set(runtime_names)
    missing = sorted(runtime_set - manifest_set)
    extra = sorted(manifest_set - runtime_set)

    if missing:
        errors.append(f"missing manifest tools: {', '.join(missing)}")
    if extra:
        errors.append(f"extra manifest tools: {', '.join(extra)}")
    if len(manifest_names) != len(runtime_names):
        errors.append(f"tool count mismatch: manifest={len(manifest_names)} runtime={len(runtime_names)}")

    return not errors, errors


def main() -> None:
    try:
        ok, errors = check()
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if ok:
        print("OK: MCPB manifest tools match runtime tools")
        return

    print("FAIL: MCPB manifest tools drifted from runtime tools", file=sys.stderr)
    for error in errors:
        print(f"  {error}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
