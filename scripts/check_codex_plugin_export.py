#!/usr/bin/env python3
"""Validate the tracked Codex plugin export surface."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BANNED_PREFIXES = (
    ".git/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "__pycache__/",
    "node_modules/",
)
BANNED_SUFFIXES = (
    ".mcpb",
    ".pyc",
)
REQUIRED_FILES = {
    ".codex-plugin/plugin.json",
    ".mcp.json",
    "skills/email-management/SKILL.md",
    "start_mcp.sh",
}


def _git_lines(repo_root: Path, *args: str) -> set[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        text=True,
        capture_output=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def tracked_export_files(repo_root: Path) -> set[str]:
    tracked = _git_lines(repo_root, "ls-files")
    deleted = _git_lines(repo_root, "ls-files", "--deleted")
    return tracked - deleted


def check(repo_root: Path | None = None) -> tuple[bool, list[str]]:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent

    files = tracked_export_files(repo_root)
    errors: list[str] = []

    missing = sorted(REQUIRED_FILES - files)
    if missing:
        errors.append(f"missing required plugin files: {', '.join(missing)}")

    banned = sorted(path for path in files if path.startswith(BANNED_PREFIXES) or path.endswith(BANNED_SUFFIXES))
    if banned:
        errors.append(f"tracked export contains local artifacts: {', '.join(banned)}")

    return not errors, errors


def main() -> None:
    try:
        ok, errors = check()
    except subprocess.CalledProcessError as exc:
        print(exc.stderr, file=sys.stderr, end="")
        sys.exit(exc.returncode)

    if ok:
        print("OK: tracked Codex plugin export is clean")
        return

    print("FAIL: tracked Codex plugin export is not clean", file=sys.stderr)
    for error in errors:
        print(f"  {error}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
