"""Tests for core AppleScript reliability improvements."""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from apple_mail_mcp.core import check_mail_app, run_applescript


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


def test_run_applescript_custom_timeout():
    """run_applescript passes custom timeout to subprocess."""
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"ok\n", stderr=b"")
    with patch("apple_mail_mcp.core.subprocess.run", return_value=fake_result) as mock_run:
        run_applescript('tell application "Finder" to return 1', timeout=90)
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 90


def test_run_applescript_default_timeout():
    """run_applescript uses 30s default timeout."""
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"ok\n", stderr=b"")
    with patch("apple_mail_mcp.core.subprocess.run", return_value=fake_result) as mock_run:
        run_applescript('tell application "Finder" to return 1')
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 30


def test_run_applescript_timeout_error_message():
    """Timeout error includes actionable guidance."""
    with patch(
        "apple_mail_mcp.core.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=30),
    ):
        try:
            run_applescript("slow script", timeout=30)
            raise AssertionError("Should have raised")
        except Exception as e:
            msg = str(e)
            assert "30s" in msg
            assert "IMAP" in msg or "narrowing" in msg
