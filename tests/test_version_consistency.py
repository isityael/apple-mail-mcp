"""Tests for release version drift guards."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_REPO_ROOT = Path(__file__).parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check_versions.py"

spec = importlib.util.spec_from_file_location("check_versions", _SCRIPT)
check_versions = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(check_versions)


def test_all_version_strings_agree():
    ok, versions = check_versions.check(_REPO_ROOT)
    assert ok, versions


def test_all_version_strings_match_expected_env(monkeypatch=None):
    if monkeypatch is None:
        os.environ["APPLE_MAIL_MCP_EXPECTED_VERSION"] = "2.6.2"
        try:
            ok, versions = check_versions.check(_REPO_ROOT)
        finally:
            os.environ.pop("APPLE_MAIL_MCP_EXPECTED_VERSION", None)
    else:
        monkeypatch.setenv("APPLE_MAIL_MCP_EXPECTED_VERSION", "2.6.2")
        ok, versions = check_versions.check(_REPO_ROOT)
    assert ok, versions


def test_package_exposes_version():
    import apple_mail_mcp

    assert apple_mail_mcp.__version__ == "2.6.2"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
