"""Tests for package metadata drift guards."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_REPO_ROOT = Path(__file__).parent.parent


def _load_script(name: str):
    script = _REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


check_mcpb_manifest = _load_script("check_mcpb_manifest")
check_codex_plugin_export = _load_script("check_codex_plugin_export")


def test_mcpb_manifest_tools_match_runtime_tools():
    ok, errors = check_mcpb_manifest.check(_REPO_ROOT)
    assert ok, errors


def test_tracked_codex_plugin_export_is_clean():
    ok, errors = check_codex_plugin_export.check(_REPO_ROOT)
    assert ok, errors


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
