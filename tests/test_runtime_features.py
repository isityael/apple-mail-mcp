"""Tests for runtime bootstrap, read-only mode, and compose/search helpers."""

from __future__ import annotations

import os
import sys

# Ensure package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import apple_mail_mcp.server as server
from apple_mail_mcp import bootstrap, mcp
from apple_mail_mcp.tools import analytics, compose, manage, search


class FakeMCP:
    """Minimal fake MCP server for bootstrap tests."""

    def __init__(self) -> None:
        self.removed: list[str] = []

    def remove_tool(self, name: str) -> None:
        self.removed.append(name)


def test_default_tool_count():
    assert len(mcp._tool_manager._tools) == 38


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


def test_inbox_dashboard_reports_packaging_state():
    result = analytics.inbox_dashboard()
    if isinstance(result, str):
        assert "Dashboard UI is not packaged" in result
        assert "analytics tools remain available" in result


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


def test_strip_cdata_wrappers():
    assert compose._strip_cdata_wrappers("<![CDATA[<p>Hello</p>]]>") == "<p>Hello</p>"
    assert compose._strip_cdata_wrappers("<p>Hello</p>]]>") == "<p>Hello</p>"


def test_html_pasteboard_script_uses_rtf_and_quoted_path():
    script = compose._html_to_pasteboard_script('/tmp/mail html_"x".html')
    assert "NSPasteboardTypeRTF" in script
    assert "RTFFromRange" in script
    assert 'do shell script "cat " & quoted form of' in script
    assert "NSPasteboardTypeHTML" not in script


def test_send_html_email_uses_run_applescript_and_pastes_before_attachments():
    old_run_applescript = compose.run_applescript
    captured: dict[str, str | int] = {}

    def fake_run_applescript(script: str, *, timeout: int = 30) -> str:
        captured["script"] = script
        captured["timeout"] = timeout
        return "HTML_SENT"

    compose.run_applescript = fake_run_applescript
    try:
        result = compose._send_html_email(
            account="Work",
            to="alice@example.com",
            subject="Status",
            body_html="<![CDATA[<p>Status</p>]]>",
            attachments_script='make new attachment with properties {file name:(POSIX file "/tmp/report.pdf")}',
            mode="draft",
        )
    finally:
        compose.run_applescript = old_run_applescript

    script = str(captured["script"])
    assert result.startswith("HTML_SENT")
    assert captured["timeout"] == 30
    assert "NSPasteboardTypeRTF" in script
    assert "<![CDATA[" not in script
    assert "focusWaited" in script
    assert script.index('keystroke "v" using command down') < script.index("make new attachment")
    assert 'do shell script "cat " & quoted form of' in script


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

    def fake_run_applescript(script: str, **kwargs) -> str:
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

    def fake_run_applescript(script: str, **kwargs) -> str:
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


def test_move_email_message_ids_uses_exact_id_condition():
    old_run_applescript = manage.run_applescript
    captured: dict[str, str] = {}

    def fake_run_applescript(script: str, **kwargs) -> str:
        captured["script"] = script
        return "MOVE_OK"

    manage.run_applescript = fake_run_applescript
    try:
        result = manage.move_email(
            account="Work",
            subject_keyword=None,
            to_mailbox="Archive",
            message_ids=["101", "bad", "202", "101"],
        )
    finally:
        manage.run_applescript = old_run_applescript

    assert result == "MOVE_OK"
    assert "(id is 101 or id is 202)" in captured["script"]
    assert "MOVING EMAILS BY IDS" in captured["script"]


def test_update_email_status_flag_color_and_message_ids():
    old_run_applescript = manage.run_applescript
    captured: dict[str, str] = {}

    def fake_run_applescript(script: str, **kwargs) -> str:
        captured["script"] = script
        return "FLAG_OK"

    manage.run_applescript = fake_run_applescript
    try:
        result = manage.update_email_status(
            account="Work",
            action="flag",
            message_ids=["101"],
            flag_color="Orange",
        )
    finally:
        manage.run_applescript = old_run_applescript

    assert result == "FLAG_OK"
    assert "set flagged status of aMessage to true" in captured["script"]
    assert "set flag index of aMessage to 1" in captured["script"]
    assert "(id is 101)" in captured["script"]


def test_search_emails_flag_color_filter_uses_flag_index():
    old_run_applescript = search.run_applescript
    old_try_imap = search._try_imap_search
    captured: dict[str, str] = {}

    def fake_run_applescript(script: str, **kwargs) -> str:
        captured["script"] = script
        return "SEARCH_OK"

    search._try_imap_search = lambda *args, **kwargs: None
    search.run_applescript = fake_run_applescript
    try:
        result = search.search_emails(account="Work", flag_color="purple")
    finally:
        search.run_applescript = old_run_applescript
        search._try_imap_search = old_try_imap

    assert result == "SEARCH_OK"
    assert "flag index is 5" in captured["script"]
    assert "messageFlagIndex" in captured["script"]


def test_synchronize_account_uses_bounded_timeout():
    old_run_applescript = manage.run_applescript
    captured: dict[str, str | int] = {}

    def fake_run_applescript(script: str, *, timeout: int = 30) -> str:
        captured["script"] = script
        captured["timeout"] = timeout
        return "SYNC_OK"

    manage.run_applescript = fake_run_applescript
    try:
        result = manage.synchronize_account("Work")
    finally:
        manage.run_applescript = old_run_applescript

    assert result == "SYNC_OK"
    assert captured["timeout"] == 60
    assert "with timeout of 8 seconds" in str(captured["script"])
    assert 'account "Work"' in str(captured["script"])


def test_imap_all_mailbox_offset_is_global():
    class FakeConn:
        def __init__(self):
            self.selected: list[str] = []

        def select(self, mailbox: str, readonly: bool = True):
            self.selected.append(mailbox.strip('"'))

        def logout(self):
            return None

    conn = FakeConn()
    old_has_config = search.imap_backend.has_imap_config
    old_get_config = search.imap_backend.get_account_config
    old_connect = search.imap_backend.connect
    old_list_folders = search.imap_backend.list_folders
    old_resolve_folder = search.imap_backend.resolve_folder
    old_build_criteria = search.imap_backend.build_imap_search_criteria
    old_imap_search = search.imap_backend.imap_search
    old_batch_fetch = search.imap_backend.batch_fetch_headers

    def fake_imap_search(fake_conn, criteria):
        folder = fake_conn.selected[-1]
        if folder == "Inbox":
            return [b"1", b"2"]
        if folder == "Archive":
            return [b"3", b"4", b"5"]
        return []

    def fake_batch_fetch(fake_conn, uids):
        return [{"subject": uid.decode(), "from": "a", "date": "d", "to": "", "cc": ""} for uid in uids]

    search.imap_backend.has_imap_config = lambda account: True
    search.imap_backend.get_account_config = lambda account: {"host": "h", "port": 1, "user": "u", "password": "p"}
    search.imap_backend.connect = lambda *args: conn
    search.imap_backend.list_folders = lambda fake_conn: ["Inbox", "Archive"]
    search.imap_backend.resolve_folder = lambda mailbox, existing: mailbox
    search.imap_backend.build_imap_search_criteria = lambda **kwargs: "ALL"
    search.imap_backend.imap_search = fake_imap_search
    search.imap_backend.batch_fetch_headers = fake_batch_fetch
    try:
        results = search._try_imap_search("Work", "All", max_results=2, offset=3)
    finally:
        search.imap_backend.has_imap_config = old_has_config
        search.imap_backend.get_account_config = old_get_config
        search.imap_backend.connect = old_connect
        search.imap_backend.list_folders = old_list_folders
        search.imap_backend.resolve_folder = old_resolve_folder
        search.imap_backend.build_imap_search_criteria = old_build_criteria
        search.imap_backend.imap_search = old_imap_search
        search.imap_backend.batch_fetch_headers = old_batch_fetch

    assert [item["subject"] for item in results] == ["4", "5"]


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
