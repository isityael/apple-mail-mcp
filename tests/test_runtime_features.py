"""Tests for runtime bootstrap, read-only mode, and compose/search helpers."""

from __future__ import annotations

import os
import sys

# Ensure package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import apple_mail_mcp.server as server
from apple_mail_mcp import bootstrap, mcp
from apple_mail_mcp.tools import compose, search


class FakeMCP:
    """Minimal fake MCP server for bootstrap tests."""

    def __init__(self) -> None:
        self.removed: list[str] = []

    def remove_tool(self, name: str) -> None:
        self.removed.append(name)


def test_default_tool_count():
    assert len(mcp._tool_manager._tools) == 37


def test_parse_args_read_only():
    args = bootstrap.parse_args(["--read-only"])
    assert args.read_only is True


def test_apply_read_only_mode_removes_send_tools():
    fake = FakeMCP()
    bootstrap.apply_read_only_mode(fake, True)
    assert fake.removed == list(bootstrap.SEND_TOOL_NAMES)


def test_configure_runtime_sets_server_flag():
    fake = FakeMCP()
    server.READ_ONLY = False

    args, loaded = bootstrap.configure_runtime(["--read-only"], package_loader=lambda: fake)

    assert args.read_only is True
    assert loaded is fake
    assert server.READ_ONLY is True
    assert fake.removed == list(bootstrap.SEND_TOOL_NAMES)

    server.READ_ONLY = False


def test_manage_drafts_send_blocked_in_read_only():
    old_value = server.READ_ONLY
    server.READ_ONLY = True
    try:
        result = compose.manage_drafts(account="Work", action="send", draft_subject="Status")
    finally:
        server.READ_ONLY = old_value

    assert "read-only mode" in result.lower()


def test_manage_drafts_list_allowed_in_read_only():
    old_read_only = server.READ_ONLY
    old_run_applescript = compose.run_applescript
    server.READ_ONLY = True
    compose.run_applescript = lambda script: "LIST_OK"
    try:
        result = compose.manage_drafts(account="Work", action="list")
    finally:
        compose.run_applescript = old_run_applescript
        server.READ_ONLY = old_read_only

    assert result == "LIST_OK"


def test_compose_email_uses_html_path_when_body_html_present():
    old_read_only = server.READ_ONLY
    old_send_html = compose._send_html_email

    captured: dict[str, str] = {}

    def fake_send_html(**kwargs: str) -> str:
        captured.update(kwargs)
        return "HTML_OK"

    server.READ_ONLY = False
    compose._send_html_email = fake_send_html
    try:
        result = compose.compose_email(
            account="Work",
            to="alice@example.com",
            subject="Status",
            body="Fallback body",
            body_html="<h1>Status</h1>",
            mode="draft",
        )
    finally:
        compose._send_html_email = old_send_html
        server.READ_ONLY = old_read_only

    assert result == "HTML_OK"
    assert captured["body_html"] == "<h1>Status</h1>"
    assert captured["mode"] == "draft"


def test_compose_email_keeps_plain_text_path_without_body_html():
    old_read_only = server.READ_ONLY
    old_send_html = compose._send_html_email
    old_run_applescript = compose.run_applescript

    captured: dict[str, str] = {}

    def fake_run_applescript(script: str) -> str:
        captured["script"] = script
        return "PLAIN_OK"

    def unexpected_html(**kwargs: str) -> str:
        raise AssertionError("HTML path should not be used")

    server.READ_ONLY = False
    compose._send_html_email = unexpected_html
    compose.run_applescript = fake_run_applescript
    try:
        result = compose.compose_email(
            account="Work",
            to="alice@example.com",
            subject="Status",
            body="Plain body",
        )
    finally:
        compose._send_html_email = old_send_html
        compose.run_applescript = old_run_applescript
        server.READ_ONLY = old_read_only

    assert result == "PLAIN_OK"
    assert 'content:"Plain body"' in captured["script"]


def test_reply_to_email_uses_temp_file_and_fixed_reply_all_syntax():
    old_read_only = server.READ_ONLY
    old_run_applescript = compose.run_applescript

    captured: dict[str, str] = {}

    def fake_run_applescript(script: str) -> str:
        captured["script"] = script
        return "REPLY_OK"

    server.READ_ONLY = False
    compose.run_applescript = fake_run_applescript
    try:
        result = compose.reply_to_email(
            account="Work",
            subject_keyword="Status",
            reply_body="Line 1\nLine 2",
            reply_to_all=True,
            mode="draft",
        )
    finally:
        compose.run_applescript = old_run_applescript
        server.READ_ONLY = old_read_only

    assert result == "REPLY_OK"
    assert "reply foundMessage with opening window and reply to all" in captured["script"]
    assert 'set replyBodyText to do shell script "cat " & quoted form of "' in captured["script"]
    assert "Line 1" not in captured["script"]


def test_search_native_body_clause_builder():
    date_setup, conditions = search._build_native_whose_clause(body="invoice", date_from="2026-03-01")
    assert 'content contains "invoice"' in conditions
    assert "date received >=" in " ".join(conditions)
    assert "set dateFrom to current date" in date_setup


def test_search_email_content_uses_native_whose_filter():
    old_run_applescript = search.run_applescript
    captured: dict[str, str] = {}

    def fake_run_applescript(script: str) -> str:
        captured["script"] = script
        return "SEARCH_OK"

    search.run_applescript = fake_run_applescript
    try:
        result = search.search_email_content(
            account="Work",
            search_text="invoice",
            search_subject=True,
            search_body=True,
        )
    finally:
        search.run_applescript = old_run_applescript

    assert result == "SEARCH_OK"
    assert 'subject contains "invoice" or content contains "invoice"' in captured["script"]


def test_search_emails_advanced_uses_native_body_filter():
    old_run_applescript = search.run_applescript
    captured: dict[str, str] = {}

    def fake_run_applescript(script: str) -> str:
        captured["script"] = script
        return "ADVANCED_OK"

    search.run_applescript = fake_run_applescript
    try:
        result = search.search_emails_advanced(
            account="Work",
            body_contains="quarterly",
            output_format="text",
        )
    finally:
        search.run_applescript = old_run_applescript

    assert result == "ADVANCED_OK"
    assert 'content contains "quarterly"' in captured["script"]
    assert "set lowerBody" not in captured["script"]


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
