"""Search tools: finding and filtering emails."""

import contextlib
import json
import logging
from typing import Any

from apple_mail_mcp import imap as imap_backend
from apple_mail_mcp.constants import SKIP_FOLDERS
from apple_mail_mcp.core import LOWERCASE_HANDLER, escape_applescript, inject_preferences, run_applescript
from apple_mail_mcp.server import mcp

_log = logging.getLogger("apple-mail-mcp.search")


# ---------------------------------------------------------------------------
# IMAP fast-path helper
# ---------------------------------------------------------------------------


def _try_imap_search(
    account: str | None,
    mailbox: str = "INBOX",
    *,
    to: str | None = None,
    cc: str | None = None,
    subject: str | None = None,
    sender: str | None = None,
    body: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    is_read: bool | None = None,
    is_flagged: bool | None = None,
    max_results: int = 50,
    offset: int = 0,
    include_content: bool = False,
) -> list[dict[str, str]] | None:
    """Try an IMAP search for *account*.  Returns ``None`` when IMAP is not
    configured (caller should fall back to AppleScript).

    On IMAP errors the exception is logged and ``None`` is returned so the
    caller transparently falls back.
    """
    if account is None:
        return None
    if not imap_backend.has_imap_config(account):
        return None

    cfg = imap_backend.get_account_config(account)
    if not cfg or not cfg.get("user") or not cfg.get("password"):
        return None

    try:
        conn = imap_backend.connect(cfg["host"], cfg["port"], cfg["user"], cfg["password"])
    except Exception as exc:
        _log.warning("IMAP connect failed for %s: %s", account, exc)
        return None

    try:
        # Resolve and select mailbox
        existing = imap_backend.list_folders(conn)
        skip = {f.lower() for f in SKIP_FOLDERS}

        if mailbox == "All":
            folders = [f for f in existing if f.lower() not in skip]
        else:
            resolved = imap_backend.resolve_folder(mailbox, existing)
            folders = [resolved]

        # Map is_read to unseen kwarg (inverted logic)
        unseen: bool | None = None
        if is_read is True:
            unseen = False  # SEEN
        elif is_read is False:
            unseen = True  # UNSEEN

        criteria = imap_backend.build_imap_search_criteria(
            to=to,
            cc=cc,
            from_addr=sender,
            subject=subject,
            body=body,
            since=date_from,
            before=date_to,
            unseen=unseen,
            flagged=is_flagged,
        )

        all_results: list[dict[str, str]] = []

        for folder in folders:
            try:
                conn.select(f'"{folder}"', readonly=True)
            except Exception:
                continue

            uids = imap_backend.imap_search(conn, criteria)
            if not uids:
                continue

            # Apply offset + limit
            end = offset + max_results - len(all_results)
            selected = uids[offset:end] if offset else uids[: max_results - len(all_results)]
            if not selected:
                continue

            headers = imap_backend.batch_fetch_headers(conn, selected)
            for hdr in headers:
                all_results.append(
                    {
                        "subject": str(hdr.get("subject", "")),
                        "sender": str(hdr.get("from", "")),
                        "date": str(hdr.get("date", "")),
                        "to": str(hdr.get("to", "")),
                        "cc": str(hdr.get("cc", "")),
                        "account": account,
                        "mailbox": folder,
                    }
                )
                if len(all_results) >= max_results:
                    break

            if len(all_results) >= max_results:
                break

        return all_results

    except Exception as exc:
        _log.warning("IMAP search failed for %s: %s", account, exc)
        return None
    finally:
        with contextlib.suppress(Exception):
            conn.logout()


def _format_imap_results(
    results: list[dict[str, str]],
    output_format: str = "text",
    title: str = "SEARCH RESULTS",
) -> str:
    """Format IMAP search results as text or JSON."""
    if output_format == "json":
        return json.dumps(results, indent=2)

    if not results:
        return f"{title}\n\nNo emails found."

    lines = [title, ""]
    for r in results:
        lines.append(f"\u2709 {r.get('subject', '(no subject)')}")
        lines.append(f"   From: {r.get('sender', '')}")
        if r.get("to"):
            lines.append(f"   To: {r['to']}")
        lines.append(f"   Date: {r.get('date', '')}")
        lines.append(f"   Account: {r.get('account', '')}")
        if r.get("mailbox"):
            lines.append(f"   Mailbox: {r['mailbox']}")
        lines.append("")

    lines.append("=" * 40)
    lines.append(f"FOUND: {len(results)} email(s)")
    lines.append("=" * 40)
    return "\n".join(lines)


def _build_native_whose_clause(
    *,
    subject: str | None = None,
    sender: str | None = None,
    body: str | None = None,
    read_status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[str, list[str]]:
    """Build date setup and `whose` conditions for native Mail.app filtering."""
    conditions: list[str] = []
    date_setup = ""

    if subject:
        conditions.append(f'subject contains "{escape_applescript(subject)}"')
    if sender:
        conditions.append(f'sender contains "{escape_applescript(sender)}"')
    if body:
        conditions.append(f'content contains "{escape_applescript(body)}"')

    if read_status == "read":
        conditions.append("read status is true")
    elif read_status == "unread":
        conditions.append("read status is false")

    if date_from:
        y, m, d = date_from.split("-")
        date_setup += f"""
            set dateFrom to current date
            set year of dateFrom to {int(y)}
            set month of dateFrom to {int(m)}
            set day of dateFrom to {int(d)}
            set time of dateFrom to 0
        """
        conditions.append("date received >= dateFrom")

    if date_to:
        y, m, d = date_to.split("-")
        date_setup += f"""
            set dateTo to current date
            set year of dateTo to {int(y)}
            set month of dateTo to {int(m)}
            set day of dateTo to {int(d)}
            set time of dateTo to 86399
        """
        conditions.append("date received <= dateTo")

    return date_setup, conditions


@mcp.tool()
@inject_preferences
def get_email_with_content(
    account: str, subject_keyword: str, max_results: int = 5, max_content_length: int = 300, mailbox: str = "INBOX"
) -> str:
    """
    Search for emails by subject keyword and return with full content preview.

    Args:
        account: Account name to search in (e.g., "Gmail", "Work")
        subject_keyword: Keyword to search for in email subjects
        max_results: Maximum number of matching emails to return (default: 5)
        max_content_length: Maximum content length in characters (default: 300, 0 = unlimited)
        mailbox: Mailbox to search (default: "INBOX", use "All" for all mailboxes)

    Returns:
        Detailed email information including content preview
    """
    # --- IMAP fast path ---
    imap_results = _try_imap_search(
        account,
        mailbox,
        subject=subject_keyword,
        max_results=max_results,
    )
    if imap_results is not None:
        return _format_imap_results(
            imap_results, output_format="text", title=f"SEARCH RESULTS FOR: {subject_keyword} (IMAP)"
        )

    # --- AppleScript fallback ---
    # Escape user inputs for AppleScript
    escaped_keyword = escape_applescript(subject_keyword)
    escaped_account = escape_applescript(account)
    escaped_mailbox = escape_applescript(mailbox)

    # Build skip-folders condition for "All" mailbox
    from apple_mail_mcp.core import skip_folders_condition

    skip_cond = skip_folders_condition("mailboxName")

    # Build mailbox selection logic
    if mailbox == "All":
        mailbox_script = f"""
            set allMailboxes to every mailbox of targetAccount
            repeat with currentMailbox in allMailboxes
                try
                    set mailboxName to name of currentMailbox
                    if {skip_cond} then
                        set searchMailboxes to {{currentMailbox}}
        """
        mailbox_end = f"""
                    end if
                end try
                if resultCount >= {max_results} then exit repeat
            end repeat
        """
        search_location = "all mailboxes"
    else:
        mailbox_script = f'''
            try
                set currentMailbox to mailbox "{escaped_mailbox}" of targetAccount
            on error
                if "{escaped_mailbox}" is "INBOX" then
                    set currentMailbox to mailbox "Inbox" of targetAccount
                else
                    error "Mailbox not found: {escaped_mailbox}"
                end if
            end try
            set mailboxName to name of currentMailbox
            if true then
                set searchMailboxes to {{currentMailbox}}
        '''
        mailbox_end = """
            end if
        """
        search_location = mailbox

    script = f'''
    tell application "Mail"
        set outputText to "SEARCH RESULTS FOR: {escaped_keyword}" & return
        set outputText to outputText & "Searching in: {search_location}" & return & return
        set resultCount to 0

        try
            set targetAccount to account "{escaped_account}"
            {mailbox_script}

                        -- Use whose clause for fast indexed filtering
                        set matchedMessages to (every message of currentMailbox whose subject contains "{escaped_keyword}")

                        repeat with aMessage in matchedMessages
                            if resultCount >= {max_results} then exit repeat

                            try
                                set messageSubject to subject of aMessage
                                set messageSender to sender of aMessage
                                set messageDate to date received of aMessage
                                set messageRead to read status of aMessage

                                if messageRead then
                                    set readIndicator to "\u2713"
                                else
                                    set readIndicator to "\u2709"
                                end if

                                set outputText to outputText & readIndicator & " " & messageSubject & return
                                set outputText to outputText & "   From: " & messageSender & return
                                set outputText to outputText & "   Date: " & (messageDate as string) & return
                                set outputText to outputText & "   Mailbox: " & mailboxName & return

                                -- Get content preview
                                try
                                    set msgContent to content of aMessage
                                    set AppleScript's text item delimiters to {{return, linefeed}}
                                    set contentParts to text items of msgContent
                                    set AppleScript's text item delimiters to " "
                                    set cleanText to contentParts as string
                                    set AppleScript's text item delimiters to ""

                                    -- Handle content length limit (0 = unlimited)
                                    if {max_content_length} > 0 and length of cleanText > {max_content_length} then
                                        set contentPreview to text 1 thru {max_content_length} of cleanText & "..."
                                    else
                                        set contentPreview to cleanText
                                    end if

                                    set outputText to outputText & "   Content: " & contentPreview & return
                                on error
                                    set outputText to outputText & "   Content: [Not available]" & return
                                end try

                                set outputText to outputText & return
                                set resultCount to resultCount + 1
                            end try
                        end repeat

            {mailbox_end}

            set outputText to outputText & "========================================" & return
            set outputText to outputText & "FOUND: " & resultCount & " matching email(s)" & return
            set outputText to outputText & "========================================" & return

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''

    result = run_applescript(script, timeout=60)
    return result


@mcp.tool()
@inject_preferences
def search_emails(
    account: str,
    mailbox: str = "INBOX",
    subject_keyword: str | None = None,
    sender: str | None = None,
    has_attachments: bool | None = None,
    read_status: str = "all",
    date_from: str | None = None,
    date_to: str | None = None,
    include_content: bool = False,
    max_results: int = 20,
    output_format: str = "text",
) -> str:
    """
    Unified search tool - search emails with advanced filtering across any mailbox.

    Args:
        account: Account name to search in (e.g., "Gmail", "Work")
        mailbox: Mailbox to search (default: "INBOX", use "All" for all mailboxes, or specific folder name)
        subject_keyword: Optional keyword to search in subject
        sender: Optional sender email or name to filter by
        has_attachments: Optional filter for emails with attachments (True/False/None)
        read_status: Filter by read status: "all", "read", "unread" (default: "all")
        date_from: Optional start date filter (format: "YYYY-MM-DD")
        date_to: Optional end date filter (format: "YYYY-MM-DD")
        include_content: Whether to include email content preview (slower)
        max_results: Maximum number of results to return (default: 20)
        output_format: "text" (default, human-readable) or "json" (structured list of email dicts)

    Returns:
        Formatted list of matching emails with all requested details
    """
    # --- IMAP fast path ---
    imap_results = _try_imap_search(
        account,
        mailbox,
        subject=subject_keyword,
        sender=sender,
        date_from=date_from,
        date_to=date_to,
        is_read=True if read_status == "read" else (False if read_status == "unread" else None),
        max_results=max_results,
        include_content=include_content,
    )
    if imap_results is not None:
        return _format_imap_results(imap_results, output_format, title="SEARCH RESULTS (IMAP)")

    # --- AppleScript fallback ---
    # Escape user inputs for AppleScript
    escaped_account = escape_applescript(account)
    escaped_mailbox = escape_applescript(mailbox)
    escaped_subject = escape_applescript(subject_keyword) if subject_keyword else None
    escaped_sender = escape_applescript(sender) if sender else None

    # Build 'whose' clause conditions for fast app-level filtering
    whose_conditions = []

    if subject_keyword:
        whose_conditions.append(f'subject contains "{escaped_subject}"')

    if sender:
        whose_conditions.append(f'sender contains "{escaped_sender}"')

    if read_status == "read":
        whose_conditions.append("read status is true")
    elif read_status == "unread":
        whose_conditions.append("read status is false")

    # Build date objects programmatically (locale-independent)
    date_setup_script = ""
    if date_from:
        y, m, d = date_from.split("-")
        date_setup_script += f"""
            set dateFrom to current date
            set year of dateFrom to {int(y)}
            set month of dateFrom to {int(m)}
            set day of dateFrom to {int(d)}
            set time of dateFrom to 0
        """
        whose_conditions.append("date received >= dateFrom")
    if date_to:
        y, m, d = date_to.split("-")
        date_setup_script += f"""
            set dateTo to current date
            set year of dateTo to {int(y)}
            set month of dateTo to {int(m)}
            set day of dateTo to {int(d)}
            set time of dateTo to 86399
        """
        whose_conditions.append("date received <= dateTo")

    # Build the whose clause
    if whose_conditions:
        whose_clause = " and ".join(whose_conditions)
        fetch_script = f"set matchedMessages to (every message of currentMailbox whose {whose_clause})"
    else:
        fetch_script = "set matchedMessages to every message of currentMailbox"

    # has_attachments requires post-filter (can't use in whose clause)
    attachment_check_start = ""
    attachment_check_end = ""
    if has_attachments is not None:
        if has_attachments:
            attachment_check_start = "if (count of mail attachments of aMessage) > 0 then"
        else:
            attachment_check_start = "if (count of mail attachments of aMessage) = 0 then"
        attachment_check_end = "end if"

    # Handle content preview
    content_script = (
        """
        try
            set msgContent to content of aMessage
            set AppleScript's text item delimiters to {{return, linefeed}}
            set contentParts to text items of msgContent
            set AppleScript's text item delimiters to " "
            set cleanText to contentParts as string
            set AppleScript's text item delimiters to ""

            if length of cleanText > 300 then
                set contentPreview to text 1 thru 300 of cleanText & "..."
            else
                set contentPreview to cleanText
            end if

            set outputText to outputText & "   Content: " & contentPreview & return
        on error
            set outputText to outputText & "   Content: [Not available]" & return
        end try
    """
        if include_content
        else ""
    )

    # Build skip folders list from constants
    skip_folders_list = ", ".join(f'"{f}"' for f in SKIP_FOLDERS)

    # Build mailbox selection logic
    if mailbox == "All":
        mailbox_script = """
            set allMailboxes to every mailbox of targetAccount
            set searchMailboxes to allMailboxes
        """
    else:
        mailbox_script = f'''
            try
                set searchMailbox to mailbox "{escaped_mailbox}" of targetAccount
            on error
                if "{escaped_mailbox}" is "INBOX" then
                    set searchMailbox to mailbox "Inbox" of targetAccount
                else
                    error "Mailbox not found: {escaped_mailbox}"
                end if
            end try
            set searchMailboxes to {{searchMailbox}}
        '''

    script = f'''
    tell application "Mail"
        set outputText to "SEARCH RESULTS" & return & return
        set outputText to outputText & "Searching in: {escaped_mailbox}" & return
        set outputText to outputText & "Account: {escaped_account}" & return & return
        set resultCount to 0

        try
            set targetAccount to account "{escaped_account}"
            {date_setup_script}
            {mailbox_script}

            repeat with currentMailbox in searchMailboxes
                try
                    set mailboxName to name of currentMailbox

                    -- Skip system folders
                    set skipFolders to {{{skip_folders_list}}}
                    set shouldSkip to false
                    repeat with skipFolder in skipFolders
                        if mailboxName is skipFolder then
                            set shouldSkip to true
                            exit repeat
                        end if
                    end repeat

                    if not shouldSkip then
                        -- Use whose clause for fast app-level filtering
                        {fetch_script}

                        repeat with aMessage in matchedMessages
                            if resultCount >= {max_results} then exit repeat

                            try
                                {attachment_check_start}
                                    set messageSubject to subject of aMessage
                                    set messageSender to sender of aMessage
                                    set messageDate to date received of aMessage
                                    set messageRead to read status of aMessage

                                    set readIndicator to "\u2709"
                                    if messageRead then
                                        set readIndicator to "\u2713"
                                    end if

                                    set outputText to outputText & readIndicator & " " & messageSubject & return
                                    set outputText to outputText & "   From: " & messageSender & return
                                    set outputText to outputText & "   Date: " & (messageDate as string) & return
                                    set outputText to outputText & "   Mailbox: " & mailboxName & return

                                    {content_script}

                                    set outputText to outputText & return
                                    set resultCount to resultCount + 1
                                {attachment_check_end}
                            end try
                        end repeat
                    end if
                on error
                    -- Skip mailboxes that throw errors (smart mailboxes, missing values, etc.)
                end try
            end repeat

            set outputText to outputText & "========================================" & return
            set outputText to outputText & "FOUND: " & resultCount & " matching email(s)" & return
            set outputText to outputText & "========================================" & return

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''

    result = run_applescript(script, timeout=60)

    if output_format == "json":
        # Re-run with pipe-delimited output for structured parsing
        return _search_emails_json(
            account,
            mailbox,
            subject_keyword,
            sender,
            has_attachments,
            read_status,
            max_results,
        )

    return result


def _search_emails_json(
    account: str,
    mailbox: str,
    subject_keyword: str | None,
    sender: str | None,
    has_attachments: bool | None,
    read_status: str,
    max_results: int,
) -> str:
    """Return search results as JSON."""
    escaped_account = escape_applescript(account)
    escaped_mailbox = escape_applescript(mailbox)
    escaped_subject = escape_applescript(subject_keyword) if subject_keyword else None
    escaped_sender = escape_applescript(sender) if sender else None

    conditions = []
    if subject_keyword:
        conditions.append(f'messageSubject contains "{escaped_subject}"')
    if sender:
        conditions.append(f'messageSender contains "{escaped_sender}"')
    if has_attachments is not None:
        if has_attachments:
            conditions.append("(count of mail attachments of aMessage) > 0")
        else:
            conditions.append("(count of mail attachments of aMessage) = 0")
    if read_status == "read":
        conditions.append("messageRead is true")
    elif read_status == "unread":
        conditions.append("messageRead is false")
    condition_str = " and ".join(conditions) if conditions else "true"

    if mailbox == "All":
        mailbox_script = """
            set allMailboxes to every mailbox of targetAccount
            set searchMailboxes to allMailboxes
        """
    else:
        mailbox_script = f'''
            try
                set searchMailbox to mailbox "{escaped_mailbox}" of targetAccount
            on error
                if "{escaped_mailbox}" is "INBOX" then
                    set searchMailbox to mailbox "Inbox" of targetAccount
                else
                    error "Mailbox not found: {escaped_mailbox}"
                end if
            end try
            set searchMailboxes to {{searchMailbox}}
        '''

    script = f'''
    tell application "Mail"
        set resultLines to {{}}
        set resultCount to 0
        try
            set targetAccount to account "{escaped_account}"
            {mailbox_script}
            repeat with currentMailbox in searchMailboxes
                try
                    set mailboxName to name of currentMailbox
                    set skipFolders to {{"Trash", "Junk", "Junk Email", "Deleted Items", "Sent", "Sent Items", "Sent Messages", "Drafts", "Spam", "Deleted Messages"}}
                    set shouldSkip to false
                    repeat with skipFolder in skipFolders
                        if mailboxName is skipFolder then
                            set shouldSkip to true
                            exit repeat
                        end if
                    end repeat
                    if not shouldSkip then
                        set mailboxMessages to every message of currentMailbox
                        repeat with aMessage in mailboxMessages
                            if resultCount >= {max_results} then exit repeat
                            try
                                set messageSubject to subject of aMessage
                                set messageSender to sender of aMessage
                                set messageDate to date received of aMessage
                                set messageRead to read status of aMessage
                                if {condition_str} then
                                    set end of resultLines to messageSubject & "|||" & messageSender & "|||" & (messageDate as string) & "|||" & messageRead & "|||" & "{escaped_account}" & "|||" & mailboxName
                                    set resultCount to resultCount + 1
                                end if
                            end try
                        end repeat
                    end if
                end try
            end repeat
        on error errMsg
            return "Error: " & errMsg
        end try
        set AppleScript's text item delimiters to linefeed
        return resultLines as string
    end tell
    '''
    raw = run_applescript(script, timeout=60)
    emails = []
    if raw:
        for line in raw.split("\n"):
            if "|||" not in line:
                continue
            parts = line.split("|||")
            if len(parts) >= 5:
                emails.append(
                    {
                        "subject": parts[0].strip(),
                        "sender": parts[1].strip(),
                        "date": parts[2].strip(),
                        "is_read": parts[3].strip().lower() == "true",
                        "account": parts[4].strip(),
                        "mailbox": parts[5].strip() if len(parts) > 5 else "",
                    }
                )
    return json.dumps(emails, indent=2)


@mcp.tool()
@inject_preferences
def search_by_sender(
    sender: str,
    account: str | None = None,
    days_back: int = 30,
    max_results: int = 20,
    include_content: bool = True,
    max_content_length: int = 500,
    mailbox: str = "INBOX",
) -> str:
    """
    Find all emails from a specific sender across one or all accounts.
    Perfect for tracking newsletters, contacts, or communications from specific people/organizations.

    Args:
        sender: Sender name or email to search for (partial match, e.g., "alphasignal" or "john@")
        account: Optional account name. If None, searches all accounts.
        days_back: Only search emails from the last N days (default: 30, 0 = all time)
        max_results: Maximum number of emails to return (default: 20)
        include_content: Whether to include email content preview (default: True)
        max_content_length: Maximum length of content preview (default: 500)
        mailbox: Mailbox to search (default: "INBOX", use "All" for all mailboxes)

    Returns:
        Formatted list of emails from the sender, sorted by date (newest first)
    """
    # --- IMAP fast path ---
    from datetime import datetime, timedelta

    imap_since = None
    if days_back > 0:
        imap_since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    imap_results = _try_imap_search(
        account,
        mailbox,
        sender=sender,
        date_from=imap_since,
        max_results=max_results,
    )
    if imap_results is not None:
        return _format_imap_results(imap_results, output_format="text", title=f"EMAILS FROM SENDER: {sender} (IMAP)")

    # --- AppleScript fallback ---
    # Escape user inputs for AppleScript
    escaped_sender = escape_applescript(sender)
    escaped_mailbox = escape_applescript(mailbox)
    search_all_mailboxes = mailbox == "All"

    # Build 'whose' clause for fast app-level filtering
    whose_parts = [f'sender contains "{escaped_sender}"']
    if days_back > 0:
        date_setup = f"set targetDate to (current date) - ({days_back} * days)"
        whose_parts.append("date received > targetDate")
    else:
        date_setup = ""

    whose_clause = " and ".join(whose_parts)

    # Build content preview script
    content_script = ""
    if include_content:
        content_script = f"""
                                    try
                                        set msgContent to content of aMessage
                                        set AppleScript's text item delimiters to {{return, linefeed}}
                                        set contentParts to text items of msgContent
                                        set AppleScript's text item delimiters to " "
                                        set cleanText to contentParts as string
                                        set AppleScript's text item delimiters to ""

                                        if {max_content_length} > 0 and length of cleanText > {max_content_length} then
                                            set contentPreview to text 1 thru {max_content_length} of cleanText & "..."
                                        else
                                            set contentPreview to cleanText
                                        end if

                                        set outputText to outputText & "   Content: " & contentPreview & return
                                    on error
                                        set outputText to outputText & "   Content: [Not available]" & return
                                    end try
        """

    # Build mailbox selection: INBOX-only (fast) vs all mailboxes
    if search_all_mailboxes:
        mailbox_loop_start = """
                set accountMailboxes to every mailbox of anAccount
                repeat with aMailbox in accountMailboxes
                    try
                        set mailboxName to name of aMailbox
                        -- Skip system and aggregate folders to avoid scanning huge mailboxes
                        if mailboxName is not in {"Trash", "Junk", "Junk Email", "Deleted Items", "Deleted Messages", "Spam", "Drafts", "Sent", "Sent Items", "Sent Messages", "Sent Mail", "All Mail", "Bin"} then
        """
        mailbox_loop_end = f"""
                            if resultCount >= {max_results} then exit repeat
                        end if
                    on error
                        -- Skip individual mailboxes that throw errors (smart mailboxes, missing values, etc.)
                    end try
                end repeat
        """
    else:
        mailbox_loop_start = f'''
                -- Fast path: only search the target mailbox
                try
                    set aMailbox to mailbox "{escaped_mailbox}" of anAccount
                on error
                    if "{escaped_mailbox}" is "INBOX" then
                        set aMailbox to mailbox "Inbox" of anAccount
                    else
                        error "Mailbox not found: {escaped_mailbox}"
                    end if
                end try
                set mailboxName to name of aMailbox
                if true then
        '''
        mailbox_loop_end = """
                end if
        """

    # Build account iteration: direct access (fast) vs all accounts
    if account:
        escaped_account = escape_applescript(account)
        account_loop_start = f'''
        set anAccount to account "{escaped_account}"
        set accountName to name of anAccount
        repeat 1 times
        '''
        account_loop_end = """
        end repeat
        """
    else:
        account_loop_start = """
        set allAccounts to every account
        repeat with anAccount in allAccounts
            set accountName to name of anAccount
        """
        account_loop_end = f"""
            if resultCount >= {max_results} then exit repeat
        end repeat
        """

    script = f"""
    tell application "Mail"
        set outputText to "EMAILS FROM SENDER: {escaped_sender}" & return
        set outputText to outputText & "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501" & return & return
        set resultCount to 0

        {date_setup}

        {account_loop_start}

            try
                {mailbox_loop_start}

                        -- Use whose clause for fast app-level filtering
                        set matchedMessages to (every message of aMailbox whose {whose_clause})

                        repeat with aMessage in matchedMessages
                            if resultCount >= {max_results} then exit repeat

                            try
                                    set messageSubject to subject of aMessage
                                    set messageSender to sender of aMessage
                                    set messageDate to date received of aMessage
                                    set messageRead to read status of aMessage

                                    if messageRead then
                                        set readIndicator to "\u2713"
                                    else
                                        set readIndicator to "\u2709"
                                    end if

                                    set outputText to outputText & readIndicator & " " & messageSubject & return
                                    set outputText to outputText & "   From: " & messageSender & return
                                    set outputText to outputText & "   Date: " & (messageDate as string) & return
                                    set outputText to outputText & "   Account: " & accountName & return
                                    set outputText to outputText & "   Mailbox: " & mailboxName & return

                                    {content_script}

                                    set outputText to outputText & return
                                    set resultCount to resultCount + 1
                            end try
                        end repeat

                {mailbox_loop_end}

            on error errMsg
                set outputText to outputText & "\u26a0 Error accessing mailboxes for " & accountName & ": " & errMsg & return
            end try

        {account_loop_end}

        set outputText to outputText & "========================================" & return
        set outputText to outputText & "FOUND: " & resultCount & " email(s) from sender" & return
        if {days_back} > 0 then
            set outputText to outputText & "Time range: Last {days_back} days" & return
        end if
        set outputText to outputText & "========================================" & return

        return outputText
    end tell
    """

    result = run_applescript(script, timeout=60)
    return result


@mcp.tool()
@inject_preferences
def search_email_content(
    account: str,
    search_text: str,
    mailbox: str = "INBOX",
    search_subject: bool = True,
    search_body: bool = True,
    max_results: int = 10,
    max_content_length: int = 600,
) -> str:
    """
    Search email body content (and optionally subject).
    This is slower than subject-only search but finds more relevant results.

    Args:
        account: Account name to search in
        search_text: Text to search for in email content
        mailbox: Mailbox to search (default: "INBOX")
        search_subject: Also search in subject line (default: True)
        search_body: Search in email body (default: True)
        max_results: Maximum results to return (default: 10, keep low as this is slow)
        max_content_length: Max content preview length (default: 600)

    Returns:
        Emails where the search text appears in body and/or subject
    """
    # --- IMAP fast path ---
    imap_results = _try_imap_search(
        account,
        mailbox,
        subject=search_text if search_subject else None,
        body=search_text if search_body else None,
        max_results=max_results,
    )
    if imap_results is not None:
        return _format_imap_results(imap_results, output_format="text", title=f"CONTENT SEARCH: {search_text} (IMAP)")

    # --- AppleScript fallback ---
    escaped_search = escape_applescript(search_text)
    escaped_account = escape_applescript(account)
    escaped_mailbox = escape_applescript(mailbox)

    search_conditions = []
    if search_subject:
        search_conditions.append(f'subject contains "{escaped_search}"')
    if search_body:
        search_conditions.append(f'content contains "{escaped_search}"')
    native_filter = " or ".join(search_conditions) if search_conditions else "false"

    script = f'''
    tell application "Mail"
        set outputText to "\U0001f50e CONTENT SEARCH: {escaped_search}" & return
        set outputText to outputText & "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501" & return
        set outputText to outputText & "\u26a0 Native Mail body filtering in use" & return & return
        set resultCount to 0
        try
            set targetAccount to account "{escaped_account}"
            try
                set targetMailbox to mailbox "{escaped_mailbox}" of targetAccount
            on error
                if "{escaped_mailbox}" is "INBOX" then
                    set targetMailbox to mailbox "Inbox" of targetAccount
                else
                    error "Mailbox not found: {escaped_mailbox}"
                end if
            end try
            set matchedMessages to (every message of targetMailbox whose {native_filter})
            repeat with aMessage in matchedMessages
                if resultCount >= {max_results} then exit repeat
                try
                    set messageSubject to subject of aMessage
                    set messageSender to sender of aMessage
                    set messageDate to date received of aMessage
                    set messageRead to read status of aMessage
                    set msgContent to ""
                    try
                        set msgContent to content of aMessage
                    end try
                    if messageRead then
                        set readIndicator to "\u2713"
                    else
                        set readIndicator to "\u2709"
                    end if
                    set outputText to outputText & readIndicator & " " & messageSubject & return
                    set outputText to outputText & "   From: " & messageSender & return
                    set outputText to outputText & "   Date: " & (messageDate as string) & return
                    set outputText to outputText & "   Mailbox: {escaped_mailbox}" & return
                    try
                        set AppleScript's text item delimiters to {{return, linefeed}}
                        set contentParts to text items of msgContent
                        set AppleScript's text item delimiters to " "
                        set cleanText to contentParts as string
                        set AppleScript's text item delimiters to ""
                        if length of cleanText > {max_content_length} then
                            set contentPreview to text 1 thru {max_content_length} of cleanText & "..."
                        else
                            set contentPreview to cleanText
                        end if
                        set outputText to outputText & "   Content: " & contentPreview & return
                    on error
                        set outputText to outputText & "   Content: [Not available]" & return
                    end try
                    set outputText to outputText & return
                    set resultCount to resultCount + 1
                end try
            end repeat
            set outputText to outputText & "========================================" & return
            set outputText to outputText & "FOUND: " & resultCount & " email(s) matching \\"{escaped_search}\\"" & return
            set outputText to outputText & "========================================" & return
        on error errMsg
            return "Error: " & errMsg
        end try
        return outputText
    end tell
    '''
    result = run_applescript(script, timeout=60)
    return result


@mcp.tool()
@inject_preferences
def get_newsletters(
    account: str | None = None,
    days_back: int = 7,
    max_results: int = 25,
    include_content: bool = True,
    max_content_length: int = 500,
) -> str:
    """
    Find newsletter and digest emails by detecting common patterns.
    Automatically identifies emails from newsletter services and digest senders.

    Args:
        account: Account to search. If None, searches all accounts.
        days_back: Only search last N days (default: 7)
        max_results: Maximum newsletters to return (default: 25)
        include_content: Include content preview (default: True)
        max_content_length: Max preview length (default: 500)

    Returns:
        List of detected newsletter emails sorted by date
    """
    # --- IMAP fast path ---
    if account is not None:
        from datetime import datetime, timedelta

        from apple_mail_mcp.constants import NEWSLETTER_KEYWORD_PATTERNS, NEWSLETTER_PLATFORM_PATTERNS

        imap_since = None
        if days_back > 0:
            imap_since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        all_patterns = NEWSLETTER_PLATFORM_PATTERNS + NEWSLETTER_KEYWORD_PATTERNS
        all_imap_results: list[dict[str, str]] = []
        seen_subjects: set[str] = set()

        if imap_backend.has_imap_config(account):
            for pattern in all_patterns:
                results = _try_imap_search(
                    account,
                    "INBOX",
                    sender=pattern,
                    date_from=imap_since,
                    max_results=max_results - len(all_imap_results),
                )
                if results:
                    for r in results:
                        key = f"{r.get('subject', '')}|{r.get('date', '')}"
                        if key not in seen_subjects:
                            seen_subjects.add(key)
                            all_imap_results.append(r)
                if len(all_imap_results) >= max_results:
                    break

            if all_imap_results:
                return _format_imap_results(
                    all_imap_results[:max_results], output_format="text", title="NEWSLETTER DETECTION (IMAP)"
                )

    # --- AppleScript fallback ---
    # Escape user inputs for AppleScript
    escaped_account = escape_applescript(account) if account else None

    content_script = ""
    if include_content:
        content_script = f"""
                                    try
                                        set msgContent to content of aMessage
                                        set AppleScript's text item delimiters to {{return, linefeed}}
                                        set contentParts to text items of msgContent
                                        set AppleScript's text item delimiters to " "
                                        set cleanText to contentParts as string
                                        set AppleScript's text item delimiters to ""
                                        if length of cleanText > {max_content_length} then
                                            set contentPreview to text 1 thru {max_content_length} of cleanText & "..."
                                        else
                                            set contentPreview to cleanText
                                        end if
                                        set outputText to outputText & "   Content: " & contentPreview & return
                                    on error
                                        set outputText to outputText & "   Content: [Not available]" & return
                                    end try
        """

    account_filter_start = ""
    account_filter_end = ""
    if account:
        account_filter_start = f'if accountName is "{escaped_account}" then'
        account_filter_end = "end if"

    # Build whose clause with or-chains from constants
    from apple_mail_mcp.constants import NEWSLETTER_KEYWORD_PATTERNS, NEWSLETTER_PLATFORM_PATTERNS

    all_patterns = NEWSLETTER_PLATFORM_PATTERNS + NEWSLETTER_KEYWORD_PATTERNS
    sender_or_clause = " or ".join(f'sender contains "{p}"' for p in all_patterns)

    date_setup = ""
    whose_parts = [f"({sender_or_clause})"]
    if days_back > 0:
        date_setup = f"set cutoffDate to (current date) - ({days_back} * days)"
        whose_parts.append("date received > cutoffDate")

    whose_clause = " and ".join(whose_parts)

    script = f"""
    tell application "Mail"
        set outputText to "\U0001f4f0 NEWSLETTER DETECTION" & return
        set outputText to outputText & "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501" & return & return
        set resultCount to 0
        {date_setup}
        set allAccounts to every account
        repeat with anAccount in allAccounts
            set accountName to name of anAccount
            {account_filter_start}
            try
                set accountMailboxes to every mailbox of anAccount
            on error
                set accountMailboxes to {{}}
            end try

                repeat with aMailbox in accountMailboxes
                    try
                        set mailboxName to name of aMailbox
                        if mailboxName is "INBOX" or mailboxName is "Inbox" then
                            -- Use whose clause for fast newsletter detection
                            set matchedMessages to (every message of aMailbox whose {whose_clause})
                            repeat with aMessage in matchedMessages
                                if resultCount >= {max_results} then exit repeat
                                try
                                    set messageSubject to subject of aMessage
                                    set messageSender to sender of aMessage
                                    set messageDate to date received of aMessage
                                    set messageRead to read status of aMessage
                                    if messageRead then
                                        set readIndicator to "\u2713"
                                    else
                                        set readIndicator to "\u2709"
                                    end if
                                    set outputText to outputText & readIndicator & " " & messageSubject & return
                                    set outputText to outputText & "   From: " & messageSender & return
                                    set outputText to outputText & "   Date: " & (messageDate as string) & return
                                    set outputText to outputText & "   Account: " & accountName & return
                                    {content_script}
                                    set outputText to outputText & return
                                    set resultCount to resultCount + 1
                                end try
                            end repeat
                        end if
                    on error
                        -- Skip mailboxes that throw errors (smart mailboxes, etc.)
                    end try
                    if resultCount >= {max_results} then exit repeat
                end repeat

            {account_filter_end}
            if resultCount >= {max_results} then exit repeat
        end repeat
        set outputText to outputText & "========================================" & return
        set outputText to outputText & "FOUND: " & resultCount & " newsletter(s)" & return
        set outputText to outputText & "========================================" & return
        return outputText
    end tell
    """
    result = run_applescript(script, timeout=60)
    return result


@mcp.tool()
@inject_preferences
def get_recent_from_sender(
    sender: str,
    account: str | None = None,
    time_range: str = "week",
    max_results: int = 15,
    include_content: bool = True,
    max_content_length: int = 400,
    mailbox: str = "INBOX",
) -> str:
    """
    Get recent emails from a specific sender with simple, human-friendly time filters.

    Args:
        sender: Sender name or email to search for (partial match)
        account: Optional account. If None, searches all accounts.
        time_range: Human-friendly time filter:
            - "today" = last 24 hours
            - "yesterday" = yesterday only
            - "week" = last 7 days (default)
            - "month" = last 30 days
            - "all" = no time filter
        max_results: Maximum emails to return (default: 15)
        include_content: Include content preview (default: True)
        max_content_length: Max preview length (default: 400)
        mailbox: Mailbox to search (default: "INBOX", use "All" for all mailboxes)

    Returns:
        Recent emails from the specified sender within the time range
    """
    time_ranges = {"today": 1, "yesterday": 2, "week": 7, "month": 30, "all": 0}
    days_back = time_ranges.get(time_range.lower(), 7)
    is_yesterday = time_range.lower() == "yesterday"

    # --- IMAP fast path ---
    from datetime import datetime, timedelta

    imap_since = None
    if days_back > 0:
        imap_since = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    imap_results = _try_imap_search(
        account,
        mailbox,
        sender=sender,
        date_from=imap_since,
        max_results=max_results,
    )
    if imap_results is not None:
        return _format_imap_results(
            imap_results, output_format="text", title=f"EMAILS FROM: {sender} ({time_range}) (IMAP)"
        )

    # --- AppleScript fallback ---
    # Escape user inputs for AppleScript
    escaped_sender = escape_applescript(sender)
    escaped_mailbox = escape_applescript(mailbox)
    search_all_mailboxes = mailbox == "All"

    # Build 'whose' clause for fast app-level filtering
    whose_parts = [f'sender contains "{escaped_sender}"']
    if days_back > 0:
        if is_yesterday:
            date_setup = """
            set todayStart to (current date) - (time of (current date))
            set yesterdayStart to todayStart - (1 * days)
            """
            whose_parts.append("date received >= yesterdayStart")
            whose_parts.append("date received < todayStart")
        else:
            date_setup = f"set cutoffDate to (current date) - ({days_back} * days)"
            whose_parts.append("date received > cutoffDate")
    else:
        date_setup = ""

    whose_clause = " and ".join(whose_parts)

    content_script = ""
    if include_content:
        content_script = f"""
                                    try
                                        set msgContent to content of aMessage
                                        set AppleScript's text item delimiters to {{return, linefeed}}
                                        set contentParts to text items of msgContent
                                        set AppleScript's text item delimiters to " "
                                        set cleanText to contentParts as string
                                        set AppleScript's text item delimiters to ""
                                        if length of cleanText > {max_content_length} then
                                            set contentPreview to text 1 thru {max_content_length} of cleanText & "..."
                                        else
                                            set contentPreview to cleanText
                                        end if
                                        set outputText to outputText & "   Content: " & contentPreview & return
                                    on error
                                        set outputText to outputText & "   Content: [Not available]" & return
                                    end try
        """

    # Build mailbox selection: INBOX-only (fast) vs all mailboxes
    if search_all_mailboxes:
        mailbox_loop_start = """
                set accountMailboxes to every mailbox of anAccount
                repeat with aMailbox in accountMailboxes
                    try
                        set mailboxName to name of aMailbox
                        if mailboxName is not in {"Trash", "Junk", "Junk Email", "Deleted Items", "Deleted Messages", "Spam", "Drafts", "Sent", "Sent Items", "Sent Messages", "Sent Mail", "All Mail", "Bin"} then
        """
        mailbox_loop_end = f"""
                            if resultCount >= {max_results} then exit repeat
                        end if
                    on error
                        -- Skip individual mailboxes that throw errors (smart mailboxes, missing values, etc.)
                    end try
                end repeat
        """
    else:
        mailbox_loop_start = f'''
                -- Fast path: only search the target mailbox
                try
                    set aMailbox to mailbox "{escaped_mailbox}" of anAccount
                on error
                    if "{escaped_mailbox}" is "INBOX" then
                        set aMailbox to mailbox "Inbox" of anAccount
                    else
                        error "Mailbox not found: {escaped_mailbox}"
                    end if
                end try
                set mailboxName to name of aMailbox
                if true then
        '''
        mailbox_loop_end = """
                end if
        """

    # Build account iteration: direct access (fast) vs all accounts
    if account:
        escaped_account = escape_applescript(account)
        account_loop_start = f'''
        set anAccount to account "{escaped_account}"
        set accountName to name of anAccount
        repeat 1 times
        '''
        account_loop_end = """
        end repeat
        """
    else:
        account_loop_start = """
        set allAccounts to every account
        repeat with anAccount in allAccounts
            set accountName to name of anAccount
        """
        account_loop_end = f"""
            if resultCount >= {max_results} then exit repeat
        end repeat
        """

    script = f"""
    tell application "Mail"
        set outputText to "\U0001f4e7 EMAILS FROM: {escaped_sender}" & return
        set outputText to outputText & "\u23f0 Time range: {time_range}" & return
        set outputText to outputText & "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501" & return & return
        set resultCount to 0
        {date_setup}

        {account_loop_start}

            try
                {mailbox_loop_start}

                            -- Use whose clause for fast app-level filtering
                            set matchedMessages to (every message of aMailbox whose {whose_clause})

                            repeat with aMessage in matchedMessages
                                if resultCount >= {max_results} then exit repeat
                                try
                                    set messageSubject to subject of aMessage
                                    set messageSender to sender of aMessage
                                    set messageDate to date received of aMessage
                                    set messageRead to read status of aMessage

                                    if messageRead then
                                        set readIndicator to "\u2713"
                                    else
                                        set readIndicator to "\u2709"
                                    end if
                                    set outputText to outputText & readIndicator & " " & messageSubject & return
                                    set outputText to outputText & "   From: " & messageSender & return
                                    set outputText to outputText & "   Date: " & (messageDate as string) & return
                                    set outputText to outputText & "   Account: " & accountName & return
                                    {content_script}
                                    set outputText to outputText & return
                                    set resultCount to resultCount + 1
                                end try
                            end repeat

                            if resultCount >= {max_results} then exit repeat

                {mailbox_loop_end}

            on error errMsg
                set outputText to outputText & "\u26a0 Error listing mailboxes for " & accountName & ": " & errMsg & return
            end try

        {account_loop_end}

        set outputText to outputText & "========================================" & return
        set outputText to outputText & "FOUND: " & resultCount & " email(s) from sender" & return
        set outputText to outputText & "========================================" & return
        return outputText
    end tell
    """
    result = run_applescript(script, timeout=90)
    return result


@mcp.tool()
@inject_preferences
def get_email_thread(account: str, subject_keyword: str, mailbox: str = "INBOX", max_messages: int = 50) -> str:
    """
    Get an email conversation thread - all messages with the same or similar subject.

    Args:
        account: Account name (e.g., "Gmail", "Work")
        subject_keyword: Keyword to identify the thread (e.g., "Re: Project Update")
        mailbox: Mailbox to search in (default: "INBOX", use "All" for all mailboxes)
        max_messages: Maximum number of thread messages to return (default: 50)

    Returns:
        Formatted thread view with all related messages sorted by date
    """

    # Escape user inputs for AppleScript
    escaped_account = escape_applescript(account)
    escaped_mailbox = escape_applescript(mailbox)

    # For thread detection, we'll strip common prefixes
    thread_keywords = ["Re:", "Fwd:", "FW:", "RE:", "Fw:"]
    cleaned_keyword = subject_keyword
    for prefix in thread_keywords:
        cleaned_keyword = cleaned_keyword.replace(prefix, "").strip()
    escaped_keyword = escape_applescript(cleaned_keyword)

    mailbox_script = f'''
        try
            set searchMailbox to mailbox "{escaped_mailbox}" of targetAccount
        on error
            if "{escaped_mailbox}" is "INBOX" then
                set searchMailbox to mailbox "Inbox" of targetAccount
            else if "{escaped_mailbox}" is "All" then
                set searchMailboxes to every mailbox of targetAccount
                set useAllMailboxes to true
            else
                error "Mailbox not found: {escaped_mailbox}"
            end if
        end try

        if "{escaped_mailbox}" is not "All" then
            set searchMailboxes to {{searchMailbox}}
            set useAllMailboxes to false
        end if
    '''

    script = f'''
    tell application "Mail"
        set outputText to "EMAIL THREAD VIEW" & return & return
        set outputText to outputText & "Thread topic: {escaped_keyword}" & return
        set outputText to outputText & "Account: {escaped_account}" & return & return
        set threadMessages to {{}}

        try
            set targetAccount to account "{escaped_account}"
            {mailbox_script}

            -- Collect all matching messages from all mailboxes
            repeat with currentMailbox in searchMailboxes
                set mailboxMessages to every message of currentMailbox

                repeat with aMessage in mailboxMessages
                    if (count of threadMessages) >= {max_messages} then exit repeat

                    try
                        set messageSubject to subject of aMessage

                        -- Remove common prefixes for matching
                        set cleanSubject to messageSubject
                        if cleanSubject starts with "Re: " then
                            set cleanSubject to text 5 thru -1 of cleanSubject
                        end if
                        if cleanSubject starts with "Fwd: " or cleanSubject starts with "FW: " then
                            set cleanSubject to text 6 thru -1 of cleanSubject
                        end if

                        -- Check if this message is part of the thread
                        if cleanSubject contains "{escaped_keyword}" or messageSubject contains "{escaped_keyword}" then
                            set end of threadMessages to aMessage
                        end if
                    end try
                end repeat
            end repeat

            -- Display thread messages
            set messageCount to count of threadMessages
            set outputText to outputText & "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501" & return
            set outputText to outputText & "FOUND " & messageCount & " MESSAGE(S) IN THREAD" & return
            set outputText to outputText & "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501" & return & return

            repeat with aMessage in threadMessages
                try
                    set messageSubject to subject of aMessage
                    set messageSender to sender of aMessage
                    set messageDate to date received of aMessage
                    set messageRead to read status of aMessage

                    if messageRead then
                        set readIndicator to "\u2713"
                    else
                        set readIndicator to "\u2709"
                    end if

                    set outputText to outputText & readIndicator & " " & messageSubject & return
                    set outputText to outputText & "   From: " & messageSender & return
                    set outputText to outputText & "   Date: " & (messageDate as string) & return

                    -- Get content preview
                    try
                        set msgContent to content of aMessage
                        set AppleScript's text item delimiters to {{return, linefeed}}
                        set contentParts to text items of msgContent
                        set AppleScript's text item delimiters to " "
                        set cleanText to contentParts as string
                        set AppleScript's text item delimiters to ""

                        if length of cleanText > 150 then
                            set contentPreview to text 1 thru 150 of cleanText & "..."
                        else
                            set contentPreview to cleanText
                        end if

                        set outputText to outputText & "   Preview: " & contentPreview & return
                    end try

                    set outputText to outputText & return
                end try
            end repeat

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''

    result = run_applescript(script, timeout=90)
    return result


@mcp.tool()
@inject_preferences
def search_all_accounts(
    subject_keyword: str | None = None,
    sender: str | None = None,
    days_back: int = 7,
    max_results: int = 30,
    include_content: bool = True,
    max_content_length: int = 400,
) -> str:
    """
    Search across ALL email accounts at once.

    Returns consolidated results sorted by date (newest first).
    Only searches INBOX mailboxes (skips Trash, Junk, Drafts, Sent).

    Args:
        subject_keyword: Optional keyword to search in subject
        sender: Optional sender email or name to filter by
        days_back: Number of days to look back (default: 7, 0 = all time)
        max_results: Maximum total results across all accounts (default: 30)
        include_content: Whether to include email content preview (default: True)
        max_content_length: Maximum content length in characters (default: 400)

    Returns:
        Formatted list of matching emails with account name for each
    """
    # Build date filter
    date_filter = ""
    if days_back > 0:
        date_filter = f"""
            set cutoffDate to (current date) - ({days_back} * days)
            if messageDate < cutoffDate then
                set skipMessage to true
            end if
        """

    # Build subject filter
    subject_filter = ""
    if subject_keyword:
        escaped_keyword = escape_applescript(subject_keyword)
        subject_filter = f'''
            set lowerSubject to my lowercase(messageSubject)
            set lowerKeyword to my lowercase("{escaped_keyword}")
            if lowerSubject does not contain lowerKeyword then
                set skipMessage to true
            end if
        '''

    # Build sender filter
    sender_filter = ""
    if sender:
        escaped_sender = escape_applescript(sender)
        sender_filter = f'''
            set lowerSender to my lowercase(messageSender)
            set lowerSenderFilter to my lowercase("{escaped_sender}")
            if lowerSender does not contain lowerSenderFilter then
                set skipMessage to true
            end if
        '''

    # Build content retrieval
    content_retrieval = ""
    if include_content:
        content_retrieval = f"""
            try
                set messageContent to content of msg
                if length of messageContent > {max_content_length} then
                    set messageContent to text 1 thru {max_content_length} of messageContent & "..."
                end if
                -- Clean up content for display
                set messageContent to my replaceText(messageContent, return, " ")
                set messageContent to my replaceText(messageContent, linefeed, " ")
            on error
                set messageContent to "(Content unavailable)"
            end try
            set emailRecord to emailRecord & "Content: " & messageContent & linefeed
        """

    script = f"""
        {LOWERCASE_HANDLER}

        on replaceText(theText, searchStr, replaceStr)
            set AppleScript\'s text item delimiters to searchStr
            set theItems to text items of theText
            set AppleScript\'s text item delimiters to replaceStr
            set theText to theItems as text
            set AppleScript\'s text item delimiters to ""
            return theText
        end replaceText

        tell application "Mail"
            set allResults to {{}}
            set allAccounts to every account

            repeat with acct in allAccounts
                set acctName to name of acct

                -- Find INBOX mailbox
                set inboxMailbox to missing value
                try
                    set inboxMailbox to mailbox "INBOX" of acct
                on error
                    -- Try to find inbox by checking mailboxes
                    repeat with mb in mailboxes of acct
                        set mbName to name of mb
                        if mbName is "INBOX" or mbName is "Inbox" then
                            set inboxMailbox to mb
                            exit repeat
                        end if
                    end repeat
                end try

                if inboxMailbox is not missing value then
                    try
                        set msgs to messages of inboxMailbox

                        repeat with msg in msgs
                            set skipMessage to false

                            try
                                set messageSubject to subject of msg
                                set messageSender to sender of msg
                                set messageDate to date received of msg
                                set messageRead to read status of msg
                            on error
                                set skipMessage to true
                            end try

                            if not skipMessage then
                                {date_filter}
                            end if

                            if not skipMessage then
                                {subject_filter}
                            end if

                            if not skipMessage then
                                {sender_filter}
                            end if

                            if not skipMessage then
                                -- Build email record
                                set emailRecord to ""
                                set emailRecord to emailRecord & "Account: " & acctName & linefeed
                                set emailRecord to emailRecord & "Subject: " & messageSubject & linefeed
                                set emailRecord to emailRecord & "From: " & messageSender & linefeed
                                set emailRecord to emailRecord & "Date: " & (messageDate as string) & linefeed
                                if messageRead then
                                    set emailRecord to emailRecord & "Status: Read" & linefeed
                                else
                                    set emailRecord to emailRecord & "Status: UNREAD" & linefeed
                                end if
                                {content_retrieval}

                                -- Store with date for sorting
                                set end of allResults to {{emailDate:messageDate, emailText:emailRecord}}
                            end if

                            -- Check if we have enough results
                            if (count of allResults) >= {max_results} then
                                exit repeat
                            end if
                        end repeat
                    on error errMsg
                        -- Skip this account if there\'s an error
                    end try
                end if

                -- Check if we have enough results
                if (count of allResults) >= {max_results} then
                    exit repeat
                end if
            end repeat

            -- Sort results by date (newest first)
            set sortedResults to my sortByDate(allResults)

            -- Build output
            set outputText to ""
            set emailCount to count of sortedResults

            if emailCount is 0 then
                return "No emails found matching your criteria across all accounts."
            end if

            set outputText to "=== Cross-Account Search Results ===" & linefeed
            set outputText to outputText & "Found " & emailCount & " email(s)" & linefeed
            set outputText to outputText & "---" & linefeed & linefeed

            repeat with emailItem in sortedResults
                set outputText to outputText & emailText of emailItem & linefeed & "---" & linefeed
            end repeat

            return outputText
        end tell

        on sortByDate(theList)
            -- Simple bubble sort by date (descending - newest first)
            set listLength to count of theList
            repeat with i from 1 to listLength - 1
                repeat with j from 1 to listLength - i
                    if emailDate of item j of theList < emailDate of item (j + 1) of theList then
                        set temp to item j of theList
                        set item j of theList to item (j + 1) of theList
                        set item (j + 1) of theList to temp
                    end if
                end repeat
            end repeat
            return theList
        end sortByDate
    """

    result = run_applescript(script, timeout=90)
    return result


@mcp.tool()
@inject_preferences
def search_emails_advanced(
    account: str | None = None,
    mailbox: str = "INBOX",
    subject_contains: str | None = None,
    body_contains: str | None = None,
    sender_contains: str | None = None,
    to_contains: str | None = None,
    cc_contains: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    is_read: bool | None = None,
    has_attachments: bool | None = None,
    is_flagged: bool | None = None,
    max_results: int = 50,
    offset: int = 0,
    output_format: str = "text",
) -> str:
    """
    Powerful unified search across accounts and mailboxes with flexible filters.

    Combines the capabilities of search_emails, search_by_sender,
    search_email_content, and search_all_accounts into one tool.
    When *account* is None, all accounts are searched.

    Uses IMAP when available (much faster, no Mail.app freezes).
    Falls back to AppleScript for accounts without IMAP configuration.

    Args:
        account: Account to search (None = all accounts)
        mailbox: Mailbox name (default "INBOX", "All" for all mailboxes)
        subject_contains: Filter by subject keyword (case-insensitive)
        body_contains: Filter by body text (slower, case-insensitive)
        sender_contains: Filter by sender name/email (case-insensitive)
        to_contains: Filter by To recipient address (case-insensitive, IMAP-accelerated)
        cc_contains: Filter by CC recipient address (case-insensitive, IMAP-accelerated)
        date_from: Start date "YYYY-MM-DD" (inclusive)
        date_to: End date "YYYY-MM-DD" (inclusive)
        is_read: Filter by read status (True/False/None for any)
        has_attachments: Filter by attachment presence (True/False/None)
        is_flagged: Filter by flagged status (True/False/None)
        max_results: Maximum results (default 50)
        offset: Skip first N results for pagination (default 0)
        output_format: "text" (human-readable) or "json" (structured)

    Returns:
        Matching emails across the specified scope
    """
    # --- IMAP fast path ---
    imap_results = _try_imap_search(
        account,
        mailbox,
        to=to_contains,
        cc=cc_contains,
        subject=subject_contains,
        sender=sender_contains,
        body=body_contains,
        date_from=date_from,
        date_to=date_to,
        is_read=is_read,
        is_flagged=is_flagged,
        max_results=max_results,
        offset=offset,
    )
    if imap_results is not None:
        return _format_imap_results(imap_results, output_format, title="ADVANCED SEARCH RESULTS (IMAP)")

    # --- AppleScript fallback ---
    from apple_mail_mcp.core import build_mailbox_ref, skip_folders_condition

    escaped_account = escape_applescript(account) if account else None
    date_setup, whose_conditions = _build_native_whose_clause(
        subject=subject_contains,
        sender=sender_contains,
        body=body_contains,
        read_status="read" if is_read is True else "unread" if is_read is False else None,
        date_from=date_from,
        date_to=date_to,
    )
    fetch_script = (
        f"set mailboxMessages to (every message of aMailbox whose {' and '.join(whose_conditions)})"
        if whose_conditions
        else "set mailboxMessages to every message of aMailbox"
    )

    filter_lines: list[str] = []
    if has_attachments is True:
        filter_lines.append("if (count of mail attachments of aMessage) = 0 then set skipMsg to true")
    elif has_attachments is False:
        filter_lines.append("if (count of mail attachments of aMessage) > 0 then set skipMsg to true")
    if is_flagged is True:
        filter_lines.append("if not (flagged status of aMessage) then set skipMsg to true")
    elif is_flagged is False:
        filter_lines.append("if (flagged status of aMessage) then set skipMsg to true")

    # Recipient post-filter (can't use whose for nested recipient objects)
    if to_contains:
        escaped_to = escape_applescript(to_contains)
        filter_lines.append(f"""set toMatch to false
                                    repeat with r in (every to recipient of aMessage)
                                        if address of r contains "{escaped_to}" then
                                            set toMatch to true
                                            exit repeat
                                        end if
                                    end repeat
                                    if not toMatch then set skipMsg to true""")
    if cc_contains:
        escaped_cc = escape_applescript(cc_contains)
        filter_lines.append(f"""set ccMatch to false
                                    repeat with r in (every cc recipient of aMessage)
                                        if address of r contains "{escaped_cc}" then
                                            set ccMatch to true
                                            exit repeat
                                        end if
                                    end repeat
                                    if not ccMatch then set skipMsg to true""")

    filter_block = "\n                                    ".join(filter_lines)

    # Account loop
    if account:
        acct_start = f'''
        set anAccount to account "{escaped_account}"
        set accountName to name of anAccount
        repeat 1 times
'''
        acct_end = """
        end repeat
"""
    else:
        acct_start = """
        set allAccounts to every account
        repeat with anAccount in allAccounts
            set accountName to name of anAccount
"""
        acct_end = f"""
            if resultCount >= {max_results} then exit repeat
        end repeat
"""

    # Mailbox loop
    skip_cond = skip_folders_condition("mailboxName")
    if mailbox == "All":
        mbox_start = f"""
                set accountMailboxes to every mailbox of anAccount
                repeat with aMailbox in accountMailboxes
                    try
                        set mailboxName to name of aMailbox
                        if {skip_cond} then
"""
        mbox_end = f"""
                        end if
                    end try
                    if resultCount >= {max_results} then exit repeat
                end repeat
"""
    else:
        mbox_start = f"""
                {build_mailbox_ref(mailbox, account_var="anAccount", var_name="aMailbox")}
                set mailboxName to name of aMailbox
                if true then
"""
        mbox_end = """
                end if
"""

    # Output format
    if output_format == "json":
        record_script = """
                                    set end of resultLines to messageSubject & "|||" & messageSender & "|||" & (messageDate as string) & "|||" & messageRead & "|||" & accountName & "|||" & mailboxName
"""
        output_setup = "set resultLines to {}"
        output_return = """
        set AppleScript's text item delimiters to linefeed
        return resultLines as string
"""
    else:
        record_script = """
                                    if messageRead then
                                        set ri to "\\u2713"
                                    else
                                        set ri to "\\u2709"
                                    end if
                                    set outputText to outputText & ri & " " & messageSubject & return
                                    set outputText to outputText & "   From: " & messageSender & return
                                    set outputText to outputText & "   Date: " & (messageDate as string) & return
                                    set outputText to outputText & "   Account: " & accountName & return
                                    set outputText to outputText & "   Mailbox: " & mailboxName & return
                                    set outputText to outputText & return
"""
        output_setup = 'set outputText to "ADVANCED SEARCH RESULTS" & return & return'
        output_return = """
        set outputText to outputText & "========================================" & return
        set outputText to outputText & "FOUND: " & resultCount & " email(s)" & return
        set outputText to outputText & "========================================" & return
        return outputText
"""

    script = f"""
    tell application "Mail"
        {output_setup}
        set resultCount to 0
        {date_setup}

        {acct_start}

            try
                {mbox_start}

                        {fetch_script}
                        repeat with aMessage in mailboxMessages
                            if resultCount >= {max_results} then exit repeat
                            try
                                set messageSubject to subject of aMessage
                                set messageSender to sender of aMessage
                                set messageDate to date received of aMessage
                                set messageRead to read status of aMessage

                                set skipMsg to false
                                    {filter_block}
                                if not skipMsg then
                                    {record_script}
                                    set resultCount to resultCount + 1
                                end if
                            end try
                        end repeat

                {mbox_end}

            on error errMsg
                -- Skip account on error
            end try

        {acct_end}

        {output_return}
    end tell
    """

    raw = run_applescript(script, timeout=60)

    if output_format == "json":
        emails: list[dict[str, Any]] = []
        if raw:
            for line in raw.split("\n"):
                if "|||" not in line:
                    continue
                parts = line.split("|||")
                if len(parts) >= 5:
                    emails.append(
                        {
                            "subject": parts[0].strip(),
                            "sender": parts[1].strip(),
                            "date": parts[2].strip(),
                            "is_read": parts[3].strip().lower() == "true",
                            "account": parts[4].strip(),
                            "mailbox": parts[5].strip() if len(parts) > 5 else "",
                        }
                    )
        return json.dumps(emails, indent=2)

    return raw
