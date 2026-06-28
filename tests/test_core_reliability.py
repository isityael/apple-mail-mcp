"""Tests for core AppleScript reliability improvements."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from apple_mail_mcp.core import _apply_applescript_timeout, check_mail_app, run_applescript


def test_check_mail_app_running():
    """check_mail_app returns True when Mail process is found."""
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"true\n", stderr=b"")
    with patch("apple_mail_mcp.core.subprocess.run", return_value=fake_result):
        assert check_mail_app() is True


def test_check_mail_app_not_running():
    """check_mail_app returns False when Mail process is not found."""
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"false\n", stderr=b"")
    with patch("apple_mail_mcp.core.subprocess.run", return_value=fake_result):
        assert check_mail_app() is False


def test_check_mail_app_error():
    """check_mail_app returns False on subprocess error."""
    with patch("apple_mail_mcp.core.subprocess.run", side_effect=OSError("no osascript")):
        assert check_mail_app() is False


class FakePopen:
    def __init__(self, returncode=0, stdout=b"ok\n", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.communicate_input = None
        self.communicate_timeout = None
        self.killed = False
        self.waited = False

    def communicate(self, input=None, timeout=None):
        self.communicate_input = input
        self.communicate_timeout = timeout
        return self.stdout, self.stderr

    def kill(self):
        self.killed = True

    def wait(self):
        self.waited = True


def test_apply_applescript_timeout_wraps_normal_script():
    """Normal scripts get an inner AppleScript timeout."""
    script = 'tell application "Mail"\nreturn 1\nend tell'
    wrapped = _apply_applescript_timeout(script, 30)
    assert wrapped.startswith("with timeout of 25 seconds")
    assert script in wrapped
    assert wrapped.endswith("end timeout")


def test_apply_applescript_timeout_skips_use_framework_script():
    """AppleScriptObjC scripts must keep top-level use declarations."""
    script = 'use framework "Foundation"\nuse scripting additions\nreturn 1'
    assert _apply_applescript_timeout(script, 30) == script


def test_apply_applescript_timeout_skips_handler_script():
    """Handler definitions are only legal at top level."""
    script = 'on sanitize(value)\nreturn value\nend sanitize\nsanitize("x")'
    assert _apply_applescript_timeout(script, 30) == script


def test_apply_applescript_timeout_does_not_skip_on_error_clause():
    """AppleScript on error clauses are not handler definitions."""
    script = "try\nreturn 1\non error errMsg\nreturn errMsg\nend try"
    assert _apply_applescript_timeout(script, 30).startswith("with timeout of")


def test_run_applescript_custom_timeout():
    """run_applescript passes custom timeout to Popen.communicate."""
    proc = FakePopen(stdout=b"ok\n")
    with patch("apple_mail_mcp.core._popen_factory", return_value=proc):
        assert run_applescript('tell application "Finder" to return 1', timeout=90) == "ok"
    assert proc.communicate_timeout == 90
    assert b"with timeout of 85 seconds" in proc.communicate_input


def test_run_applescript_default_timeout():
    """run_applescript uses 30s default timeout."""
    proc = FakePopen(stdout=b"ok\n")
    with patch("apple_mail_mcp.core._popen_factory", return_value=proc):
        assert run_applescript('tell application "Finder" to return 1') == "ok"
    assert proc.communicate_timeout == 30


def test_run_applescript_timeout_error_message():
    """Timeout error includes actionable guidance."""

    class TimeoutPopen(FakePopen):
        def communicate(self, input=None, timeout=None):
            raise subprocess.TimeoutExpired(cmd="osascript", timeout=timeout)

    proc = TimeoutPopen()
    with patch("apple_mail_mcp.core._popen_factory", return_value=proc):
        try:
            run_applescript("slow script", timeout=30)
            raise AssertionError("Should have raised")
        except Exception as e:
            msg = str(e)
            assert "30s" in msg
            assert "IMAP" in msg or "narrowing" in msg
    assert proc.killed is True
    assert proc.waited is True


def test_run_applescript_tracks_inflight_child():
    """Popen is registered while communicate is running and removed after."""
    from apple_mail_mcp import core

    entered = threading.Event()
    release = threading.Event()
    proc = FakePopen(stdout=b"ok\n")

    def communicate(input=None, timeout=None):
        entered.set()
        release.wait(timeout=2)
        return b"ok\n", b""

    proc.communicate = communicate

    with patch("apple_mail_mcp.core._popen_factory", return_value=proc):
        result = []
        thread = threading.Thread(target=lambda: result.append(run_applescript("return 1")))
        thread.start()
        entered.wait(timeout=2)
        with core._inflight_lock:
            assert proc in core._inflight_children
        release.set()
        thread.join(timeout=2)

    assert result == ["ok"]
    with core._inflight_lock:
        assert proc not in core._inflight_children


def test_run_applescript_deregisters_after_unexpected_error():
    """Unexpected communicate errors still remove the child from tracking."""
    from apple_mail_mcp import core

    class BrokenPopen(FakePopen):
        def communicate(self, input=None, timeout=None):
            raise OSError("pipe broken")

    proc = BrokenPopen()
    with patch("apple_mail_mcp.core._popen_factory", return_value=proc):
        try:
            run_applescript("return 1")
            raise AssertionError("Should have raised")
        except Exception:
            pass

    with core._inflight_lock:
        assert proc not in core._inflight_children


def test_run_applescript_raises_on_stderr():
    """Non-zero osascript exits surface stderr."""
    proc = FakePopen(returncode=1, stdout=b"", stderr=b"bad script")
    with patch("apple_mail_mcp.core._popen_factory", return_value=proc):
        try:
            run_applescript("bad")
            raise AssertionError("Should have raised")
        except Exception as e:
            assert "bad script" in str(e)


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
