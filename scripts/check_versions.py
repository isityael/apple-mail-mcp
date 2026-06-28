#!/usr/bin/env python3
"""Check version strings across local release surfaces."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def get_versions(repo_root: Path) -> dict[str, str]:
    versions: dict[str, str] = {}

    pyproject_text = (repo_root / "pyproject.toml").read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject_text, re.MULTILINE)
    if not match:
        raise ValueError("Could not find version in pyproject.toml")
    versions["pyproject.toml"] = match.group(1)

    manifest = json.loads((repo_root / "apple-mail-mcpb" / "manifest.json").read_text())
    versions["apple-mail-mcpb/manifest.json"] = manifest["version"]

    init_text = (repo_root / "apple_mail_mcp" / "__init__.py").read_text()
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', init_text, re.MULTILINE)
    if not match:
        raise ValueError("Could not find __version__ in apple_mail_mcp/__init__.py")
    versions["apple_mail_mcp/__init__.py"] = match.group(1)

    return versions


def check(repo_root: Path | None = None) -> tuple[bool, dict[str, str]]:
    if repo_root is None:
        repo_root = Path(__file__).parent.parent
    versions = get_versions(repo_root)
    return len(set(versions.values())) == 1, versions


def main() -> None:
    try:
        ok, versions = check()
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if ok:
        version = next(iter(versions.values()))
        print(f"OK: all version strings agree: {version}")
        for label, value in versions.items():
            print(f"  {label}: {value}")
        return

    print("FAIL: version strings disagree", file=sys.stderr)
    for label, value in versions.items():
        print(f"  {label}: {value}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
