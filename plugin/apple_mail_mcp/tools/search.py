"""Search tools: finding and filtering emails."""

import json
import os
import re
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import quote

from apple_mail_mcp.server import mcp
from apple_mail_mcp.constants import FLAG_COLOR_NAMES
from apple_mail_mcp.core import (
    contains_any_condition,
    inject_preferences,
    escape_applescript,
    normalize_search_terms,
    resolve_flag_color,
    read_flag_index_script,
    run_applescript,
    LOWERCASE_HANDLER,
)


# AppleScript's `whose` clause (and a bare `every message of mailbox`) forces
# Mail to walk every message in the mailbox with no index to evaluate the
# filter — fine for a few thousand messages, but on a very large mailbox
# (issue #41: a Gmail INBOX with 90,000+ messages) that single Apple event
# can run past the AppleScript timeout while Mail's main thread is pinned,
# leaving the UI unresponsive and the tool call returning nothing useful.
#
# `count of messages of currentMailbox` is a cheap scalar read (Mail already
# tracks the count; this project already relies on the same idiom elsewhere,
# see tools/inbox.py). Above this threshold we skip materializing the whole
# mailbox and instead run a capped, early-exiting index scan (see
# BOUNDED_SCAN_MAX_MESSAGES) so the amount of work is bounded by us, not by
# the mailbox size.
WHOSE_CLAUSE_SAFE_LIMIT = 5000

# Hard cap on how many messages a bounded index-scan will examine for a
# single mailbox. Each examined message is one cheap `message N of mailbox`
# metadata read (no content/body access), so this cap keeps the worst case
# comfortably inside the existing 180s AppleScript timeout. When the cap is
# hit before the requested page is filled, the response is marked
# `scan_limited` instead of silently claiming a complete (possibly empty)
# result.
BOUNDED_SCAN_MAX_MESSAGES = 4000

# The same idea for body_text searches on very large mailboxes, where every
# examined message also costs a `content of` read, which is far slower than a
# metadata read. Measured on a 75,190-message Gmail INBOX with a term that
# never matches: 50 messages took 39 s, 100 took 140 s (older bodies are not
# cached locally, so the cost is not linear). 50 keeps a worst case well
# inside the 180 s timeout. Both caps are shared across all the mailboxes of
# one call, so mailbox="All" does not get a fresh budget per mailbox.
BOUNDED_BODY_SCAN_MAX_MESSAGES = 50


MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def _build_applescript_date(
    var_name: str, date_value: Optional[str], end_of_day: bool = False
) -> str:
    """Build AppleScript to create a date from an ISO day string."""
    if not date_value:
        return ""

    try:
        parsed_date = datetime.strptime(date_value, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"Invalid date '{date_value}'. Use YYYY-MM-DD")

    month_name = MONTH_NAMES[parsed_date.month - 1]
    seconds = 86399 if end_of_day else 0
    return f"""
                set {var_name} to current date
                set year of {var_name} to {parsed_date.year}
                set month of {var_name} to {month_name}
                set day of {var_name} to {parsed_date.day}
                set time of {var_name} to {seconds}
    """


def _parse_search_records(output: str) -> List[Dict[str, Any]]:
    """Parse structured search output into dict records."""
    if not output:
        return []

    records = []
    for line in output.splitlines():
        parts = line.split("|||", 9)
        if len(parts) < 9:
            continue

        internet_message_id = parts[1].strip()
        try:
            flag_index = int(parts[8].strip())
        except ValueError:
            flag_index = -1
        record = {
            "message_id": parts[0].strip(),
            "internet_message_id": internet_message_id,
            "subject": parts[2].strip(),
            "sender": parts[3].strip(),
            "mailbox": parts[4].strip(),
            "account": parts[5].strip(),
            "is_read": parts[6].strip().lower() == "true",
            "received_date": parts[7].strip(),
            "is_flagged": flag_index >= 0,
        }
        if flag_index in FLAG_COLOR_NAMES:
            record["flag_color"] = FLAG_COLOR_NAMES[flag_index]
        if internet_message_id:
            # Apple Mail requires: message:// scheme, angle brackets (percent-encoded),
            # and raw @ in the Message-ID. Normalize ID in case angle brackets are
            # present or missing (AppleScript returns both forms).
            msg_id = internet_message_id.strip("<>")
            record["mail_link"] = f"message://%3C{quote(msg_id, safe='@')}%3E"
        if len(parts) > 9 and parts[9].strip():
            record["content_preview"] = parts[9].strip()
        records.append(record)

    return records


def _sort_search_records(
    records: List[Dict[str, Any]], sort: str
) -> List[Dict[str, Any]]:
    """Sort records by received date."""
    reverse = sort == "date_desc"
    return sorted(
        records, key=lambda item: item.get("received_date", ""), reverse=reverse
    )


def _format_search_records_text(
    records: List[Dict[str, Any]],
    subject_only: bool = False,
    scan_limited: bool = False,
) -> str:
    """Format search records as human-readable text."""
    lines = []

    if subject_only:
        lines.append("SUBJECT SEARCH RESULTS")
        lines.append("")
        for item in records:
            lines.append(f"- {item['subject']}")
    else:
        lines.append("SEARCH RESULTS")
        lines.append("")
        for item in records:
            indicator = "\u2713" if item["is_read"] else "\u2709"
            subject_line = f"{indicator} {item['subject']}"
            if item.get("flag_color"):
                subject_line += f" \u2691 {item['flag_color']}"
            elif item.get("is_flagged"):
                subject_line += " \u2691"
            lines.append(subject_line)
            lines.append(f"   From: {item['sender']}")
            lines.append(f"   Date: {item['received_date']}")
            lines.append(f"   Mailbox: {item['mailbox']}")
            if item.get("mail_link"):
                lines.append(f"   Link: {item['mail_link']}")
            if item.get("content_preview"):
                lines.append(f"   Content: {item['content_preview']}")
            lines.append("")

    lines.append("========================================")
    lines.append(f"FOUND: {len(records)} matching email(s)")
    lines.append("========================================")

    if scan_limited:
        lines.append("")
        lines.append(
            "⚠ NOTE: a very large mailbox was only partially scanned "
            "to avoid hanging Mail.app — results may be incomplete. "
            "Narrow with date_from/date_to, sender, or a smaller mailbox "
            "for full coverage."
        )

    return "\n".join(lines)


def _build_search_response(
    records: List[Dict[str, Any]],
    offset: int,
    limit: int,
    sort: str,
    output_format: str,
    subject_only: bool = False,
    scan_limited: bool = False,
) -> str:
    """Return either JSON or text for search results."""
    sorted_records = _sort_search_records(records, sort)
    has_more = len(sorted_records) > limit
    items = sorted_records[:limit]
    next_offset = offset + len(items) if has_more else None

    if output_format == "json":
        return json.dumps(
            {
                "items": items,
                "offset": offset,
                "limit": limit,
                "returned": len(items),
                "has_more": has_more,
                "next_offset": next_offset,
                "sort": sort,
                "scan_limited": scan_limited,
            }
        )

    return _format_search_records_text(
        items, subject_only=subject_only, scan_limited=scan_limited
    )


def _search_mail_records(
    account: Optional[str] = None,
    mailbox: str = "INBOX",
    subject_terms: Optional[List[str]] = None,
    sender: Optional[str] = None,
    has_attachments: Optional[bool] = None,
    flagged: Optional[bool] = None,
    flag_color: Optional[str] = None,
    read_status: str = "all",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include_content: bool = False,
    content_length: int = 300,
    offset: int = 0,
    limit: int = 100,
    sort: str = "date_desc",
    body_text: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Return structured search records from Apple Mail, plus a scan_limited flag.

    When account is None, iterates all accounts.
    When body_text is provided, uses per-message iteration with case-insensitive
    content matching (slower than subject/sender-only searches).

    The second element of the returned tuple is ``True`` when a very large
    mailbox's bounded index-scan (see WHOSE_CLAUSE_SAFE_LIMIT /
    BOUNDED_SCAN_MAX_MESSAGES above) hit its safety cap before it could
    confirm the result is complete — callers should surface this rather than
    treating the result as authoritative.
    """
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if limit <= 0:
        return [], False
    if sort not in {"date_desc", "date_asc"}:
        raise ValueError("Invalid sort. Use: date_desc, date_asc")
    if read_status not in {"all", "read", "unread"}:
        raise ValueError("Invalid read_status. Use: all, read, unread")

    flag_index: Optional[int] = None
    if flag_color is not None:
        flag_index = resolve_flag_color(flag_color)
        if flagged is False:
            raise ValueError(
                "flag_color cannot be combined with flagged=False "
                "(a colored flag implies the message is flagged)"
            )

    escaped_sender = escape_applescript(sender) if sender else None

    # When body_text is provided, we must iterate per-message (can't use whose clause)
    use_body_search = body_text is not None

    # Build whose-clause filter conditions (only used when NOT doing body search)
    filter_conditions = []
    if not use_body_search:
        if subject_terms:
            filter_conditions.append(contains_any_condition("subject", subject_terms))
        if sender:
            filter_conditions.append(f'sender contains "{escaped_sender}"')
        if has_attachments is not None:
            if has_attachments:
                filter_conditions.append("(count of mail attachments) > 0")
            else:
                filter_conditions.append("(count of mail attachments) = 0")
        if flag_index is not None:
            # flag index survives unflagging; require the active-flag boolean
            # so residual indexes on unflagged messages don't match.
            filter_conditions.append(
                f"(flagged status is true and flag index is {flag_index})"
            )
        elif flagged is not None:
            filter_conditions.append(
                f"flagged status is {'true' if flagged else 'false'}"
            )
        if read_status == "read":
            filter_conditions.append("read status is true")
        elif read_status == "unread":
            filter_conditions.append("read status is false")
        if date_from:
            filter_conditions.append("date received >= fromDate")
        if date_to:
            filter_conditions.append("date received <= toDate")

    if filter_conditions:
        matching_messages_script = f"set matchingMessages to every message of currentMailbox whose {' and '.join(filter_conditions)}"
    else:
        matching_messages_script = (
            "set matchingMessages to every message of currentMailbox"
        )

    # Per-message equivalents of filter_conditions, for use inside the
    # bounded index-scan loop below (large mailboxes only). Mirrors
    # filter_conditions exactly but references `aMessage` explicitly instead
    # of relying on the implicit `whose` element scope.
    per_message_conditions = []
    if not use_body_search:
        if subject_terms:
            subject_checks = " or ".join(
                f'subject of aMessage contains "{escape_applescript(t)}"'
                for t in subject_terms
            )
            per_message_conditions.append(f"({subject_checks})")
        if sender:
            per_message_conditions.append(
                f'sender of aMessage contains "{escaped_sender}"'
            )
        if has_attachments is not None:
            if has_attachments:
                per_message_conditions.append(
                    "(count of mail attachments of aMessage) > 0"
                )
            else:
                per_message_conditions.append(
                    "(count of mail attachments of aMessage) = 0"
                )
        if flag_index is not None:
            per_message_conditions.append(
                f"(flagged status of aMessage is true and flag index of aMessage is {flag_index})"
            )
        elif flagged is not None:
            per_message_conditions.append(
                f"flagged status of aMessage is {'true' if flagged else 'false'}"
            )
        if read_status == "read":
            per_message_conditions.append("read status of aMessage is true")
        elif read_status == "unread":
            per_message_conditions.append("read status of aMessage is false")
        if date_from:
            per_message_conditions.append("date received of aMessage >= fromDate")
        if date_to:
            per_message_conditions.append("date received of aMessage <= toDate")

    per_message_condition_expr = (
        " and ".join(per_message_conditions) if per_message_conditions else "true"
    )

    # Scan from the end of the mailbox that matches the requested sort order,
    # so a capped scan on a huge mailbox still surfaces the most relevant
    # (newest, for the default date_desc) matches instead of the oldest ones.
    # In Mail, message 1 of a mailbox is the newest (analytics.py relies on
    # the same order), so newest-first walks the indexes upward.
    if sort == "date_desc":
        scan_from, scan_to, scan_step = "1", "totalMessageCount", "1"
    else:
        scan_from, scan_to, scan_step = "totalMessageCount", "1", "-1"

    # Bounded index-scan: used instead of matching_messages_script when the
    # mailbox is too large to safely materialize in one `whose`/`every`
    # Apple event (see WHOSE_CLAUSE_SAFE_LIMIT). Reads at most
    # BOUNDED_SCAN_MAX_MESSAGES messages by index and stops as soon as it has
    # enough matches for this mailbox's contribution to the current page
    # (offsetRemaining + collectLimit). If the cap is hit first,
    # anyMailboxScanTruncated is set so the caller is told the result may be
    # incomplete instead of silently returning less than the whole picture.
    # The scan is ordered by date received, so once it has walked past the
    # requested window nothing further can match: stop instead of reading the
    # rest of the cap one Apple event at a time (#133).
    if sort == "date_desc" and date_from and not use_body_search:
        scan_cutoff = "if (date received of aMessage) < fromDate then exit repeat"
    elif sort == "date_asc" and date_to and not use_body_search:
        scan_cutoff = "if (date received of aMessage) > toDate then exit repeat"
    else:
        scan_cutoff = ""

    bounded_scan_script = f'''
                            set totalMessageCount to count of messages of currentMailbox
                            if totalMessageCount > {WHOSE_CLAUSE_SAFE_LIMIT} then
                                set matchingMessages to {{}}
                                set scanTarget to offsetRemaining + collectLimit
                                repeat with msgIndex from {scan_from} to {scan_to} by {scan_step}
                                    set scannedTotal to scannedTotal + 1
                                    if scannedTotal > {BOUNDED_SCAN_MAX_MESSAGES} then
                                        set anyMailboxScanTruncated to true
                                        exit repeat
                                    end if
                                    try
                                        set aMessage to message msgIndex of currentMailbox
                                        {scan_cutoff}
                                        if {per_message_condition_expr} then
                                            set end of matchingMessages to aMessage
                                            if (count of matchingMessages) >= scanTarget then exit repeat
                                        end if
                                    end try
                                end repeat
                            else
                                {matching_messages_script}
                            end if
    '''

    if mailbox == "All":
        mailbox_script = """
                set searchMailboxes to every mailbox of targetAccount
        """
        skip_script = """
                        set skipFolders to {"Trash", "Junk", "Junk Email", "Deleted Items", "Sent", "Sent Items", "Sent Messages", "Drafts", "Spam", "Deleted Messages"}
                        repeat with skipFolder in skipFolders
                            if mailboxName is skipFolder then
                                set shouldSkip to true
                                exit repeat
                            end if
                        end repeat
        """
    else:
        escaped_mailbox = escape_applescript(mailbox)
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
        skip_script = ""

    date_setup = _build_applescript_date("fromDate", date_from)
    date_setup += _build_applescript_date("toDate", date_to, end_of_day=True)

    # Build account iteration
    if account:
        escaped_account = escape_applescript(account)
        account_setup = f'''
                set searchAccounts to {{account "{escaped_account}"}}
        '''
    else:
        account_setup = """
                set searchAccounts to every account
        """

    # Build body search per-message filter block
    if use_body_search:
        escaped_body = escape_applescript(body_text.lower()) if body_text else ""
        # Only pay the per-message flag-index read when a flag filter needs it.
        flag_read_script = ""
        if flag_index is not None or flagged is not None:
            flag_read_script = read_flag_index_script()
        # Build per-message conditions for subject, sender, read_status, dates, attachments
        per_msg_conditions = []
        if subject_terms:
            # Case-insensitive subject check
            subject_checks = " or ".join(
                f'lowerSubject contains "{escape_applescript(t.lower())}"'
                for t in subject_terms
            )
            per_msg_conditions.append(f"({subject_checks})")
        if sender:
            per_msg_conditions.append(f'lowerSender contains "{escape_applescript(sender.lower())}"')
        if read_status == "read":
            per_msg_conditions.append("messageRead is true")
        elif read_status == "unread":
            per_msg_conditions.append("messageRead is false")
        if date_from:
            per_msg_conditions.append("messageDate >= fromDate")
        if date_to:
            per_msg_conditions.append("messageDate <= toDate")
        if has_attachments is True:
            per_msg_conditions.append("(count of mail attachments of aMessage) > 0")
        elif has_attachments is False:
            per_msg_conditions.append("(count of mail attachments of aMessage) = 0")
        if flag_index is not None:
            per_msg_conditions.append(f"messageFlagIndex is {flag_index}")
        elif flagged is True:
            per_msg_conditions.append("messageFlagIndex is not -1")
        elif flagged is False:
            per_msg_conditions.append("messageFlagIndex is -1")

        # Body text condition is always present in body search mode
        per_msg_conditions.append(f'lowerContent contains "{escaped_body}"')

        combined_condition = " and ".join(per_msg_conditions)

        body_message_checks = f'''
                                    set messageSubject to subject of aMessage
                                    set messageSender to sender of aMessage
                                    set messageRead to read status of aMessage
                                    set messageDate to date received of aMessage
                                    {flag_read_script}
                                    set lowerSubject to my lowercase(messageSubject)
                                    set lowerSender to my lowercase(messageSender)
                                    set msgContent to ""
                                    try
                                        set msgContent to content of aMessage
                                    end try
                                    set lowerContent to my lowercase(msgContent)'''
        body_search_loop = f'''
                            set matchingMessages to {{}}
                            set totalMessageCount to count of messages of currentMailbox
                            if totalMessageCount > {WHOSE_CLAUSE_SAFE_LIMIT} then
                                set scanTarget to offsetRemaining + collectLimit
                                repeat with msgIndex from {scan_from} to {scan_to} by {scan_step}
                                    set scannedTotal to scannedTotal + 1
                                    if scannedTotal > {BOUNDED_BODY_SCAN_MAX_MESSAGES} then
                                        set anyMailboxScanTruncated to true
                                        exit repeat
                                    end if
                                    try
                                        set aMessage to message msgIndex of currentMailbox
                                        {body_message_checks}
                                        if {combined_condition} then
                                            set end of matchingMessages to aMessage
                                            if (count of matchingMessages) >= scanTarget then exit repeat
                                        end if
                                    end try
                                end repeat
                            else
                            set allMessages to every message of currentMailbox
                            repeat with aMessage in allMessages
                                if collectLimit <= 0 then exit repeat
                                try
                                    set messageSubject to subject of aMessage
                                    set messageSender to sender of aMessage
                                    set messageRead to read status of aMessage
                                    set messageDate to date received of aMessage
                                    {flag_read_script}
                                    set lowerSubject to my lowercase(messageSubject)
                                    set lowerSender to my lowercase(messageSender)
                                    set msgContent to ""
                                    try
                                        set msgContent to content of aMessage
                                    end try
                                    set lowerContent to my lowercase(msgContent)
                                    if {combined_condition} then
                                        set end of matchingMessages to aMessage
                                    end if
                                end try
                            end repeat
                            end if
        '''
    else:
        body_search_loop = ""

    # Choose the message collection strategy
    if use_body_search:
        message_collection = body_search_loop
    else:
        message_collection = bounded_scan_script

    lowercase_handler = LOWERCASE_HANDLER if use_body_search else ""

    script = f'''
    {lowercase_handler}

    on sanitize_field(value)
        try
            set valueText to value as string
        on error
            set valueText to ""
        end try

        set AppleScript's text item delimiters to {{return, linefeed, tab}}
        set valueParts to text items of valueText
        set AppleScript's text item delimiters to " "
        set valueText to valueParts as string
        set AppleScript's text item delimiters to "|||"
        set valueParts to text items of valueText
        set AppleScript's text item delimiters to " | "
        set valueText to valueParts as string
        set AppleScript's text item delimiters to ""
        return valueText
    end sanitize_field

    on pad2(numberValue)
        if numberValue < 10 then
            return "0" & (numberValue as string)
        end if
        return numberValue as string
    end pad2

    on month_number(monthValue)
        set monthValues to {{January, February, March, April, May, June, July, August, September, October, November, December}}
        repeat with monthIndex from 1 to 12
            if item monthIndex of monthValues is monthValue then
                return monthIndex
            end if
        end repeat
        return 0
    end month_number

    on iso_datetime(dateValue)
        set yearValue to year of dateValue as integer
        set monthValue to my month_number(month of dateValue)
        set dayValue to day of dateValue as integer
        set hourValue to hours of dateValue
        set minuteValue to minutes of dateValue
        set secondValue to seconds of dateValue
        return (yearValue as string) & "-" & my pad2(monthValue) & "-" & my pad2(dayValue) & "T" & my pad2(hourValue) & ":" & my pad2(minuteValue) & ":" & my pad2(secondValue)
    end iso_datetime

    tell application "Mail"
        with timeout of 180 seconds
            try
                set recordLines to {{}}
                set offsetRemaining to {offset}
                set collectLimit to {limit + 1}
                set anyMailboxScanTruncated to false
                set scannedTotal to 0
                {date_setup}
                {account_setup}

                repeat with targetAccount in searchAccounts
                    if collectLimit <= 0 then exit repeat
                    set accountName to my sanitize_field(name of targetAccount)
                    {mailbox_script}

                    repeat with currentMailbox in searchMailboxes
                        if collectLimit <= 0 then exit repeat

                        try
                            set mailboxName to my sanitize_field(name of currentMailbox)
                            set shouldSkip to false
                            {skip_script}

                            if not shouldSkip then
                                {message_collection}
                                set matchingCount to count of matchingMessages

                                if offsetRemaining >= matchingCount then
                                    set offsetRemaining to offsetRemaining - matchingCount
                                else
                                    set startIndex to offsetRemaining + 1
                                    set availableCount to matchingCount - offsetRemaining
                                    if availableCount > collectLimit then
                                        set endIndex to startIndex + collectLimit - 1
                                    else
                                        set endIndex to startIndex + availableCount - 1
                                    end if

                                    if endIndex >= startIndex then
                                        set targetMessages to items startIndex thru endIndex of matchingMessages

                                        repeat with aMessage in targetMessages
                                            try
                                                set messageId to my sanitize_field(id of aMessage)
                                                set internetMessageId to ""
                                                try
                                                    set internetMessageId to my sanitize_field(message id of aMessage)
                                                end try
                                                set messageSubject to my sanitize_field(subject of aMessage)
                                                set messageSender to my sanitize_field(sender of aMessage)
                                                set messageRead to read status of aMessage
                                                set messageDate to date received of aMessage
                                                set receivedAt to my iso_datetime(messageDate)
                                                {read_flag_index_script()}
                                                set contentPreview to ""

                                                if {str(include_content).lower()} then
                                                    try
                                                        set msgContent to content of aMessage
                                                        set AppleScript's text item delimiters to {{return, linefeed, tab}}
                                                        set contentParts to text items of msgContent
                                                        set AppleScript's text item delimiters to " "
                                                        set cleanText to contentParts as string
                                                        set AppleScript's text item delimiters to ""
                                                        if {content_length} > 0 and length of cleanText > {content_length} then
                                                            set contentPreview to my sanitize_field(text 1 thru {content_length} of cleanText & "...")
                                                        else
                                                            set contentPreview to my sanitize_field(cleanText)
                                                        end if
                                                    on error
                                                        set contentPreview to ""
                                                    end try
                                                end if

                                                set readValue to "false"
                                                if messageRead then
                                                    set readValue to "true"
                                                end if

                                                set recordLine to messageId & "|||" & internetMessageId & "|||" & messageSubject & "|||" & messageSender & "|||" & mailboxName & "|||" & accountName & "|||" & readValue & "|||" & receivedAt & "|||" & (messageFlagIndex as string) & "|||" & contentPreview
                                                set end of recordLines to recordLine
                                                set collectLimit to collectLimit - 1
                                                if collectLimit <= 0 then exit repeat
                                            end try
                                        end repeat
                                    end if

                                    set offsetRemaining to 0
                                end if
                            end if
                        on error
                            -- Skip mailboxes that cannot be searched
                        end try
                    end repeat
                end repeat

                if (count of recordLines) is 0 then
                    set outputText to ""
                else
                    set AppleScript's text item delimiters to linefeed
                    set outputText to recordLines as string
                    set AppleScript's text item delimiters to ""
                end if

                set scanLimitedFlag to "false"
                if anyMailboxScanTruncated then
                    set scanLimitedFlag to "true"
                end if
                return "SCAN_LIMITED=" & scanLimitedFlag & linefeed & outputText
            on error errMsg
                return "ERROR|||" & errMsg
            end try
        end timeout
    end tell
    '''

    result = run_applescript(script, timeout=180)
    if result.startswith("ERROR|||"):
        raise ValueError(result.split("|||", 1)[1])

    scan_limited = False
    if result.startswith("SCAN_LIMITED="):
        marker_line, _, remainder = result.partition("\n")
        scan_limited = marker_line.strip() == "SCAN_LIMITED=true"
        result = remainder

    return _parse_search_records(result), scan_limited


@mcp.tool()
@inject_preferences
def search_emails(
    account: Optional[str] = None,
    mailbox: str = "INBOX",
    subject_keyword: Optional[str] = None,
    subject_keywords: Optional[List[str]] = None,
    sender: Optional[str] = None,
    has_attachments: Optional[bool] = None,
    flagged: Optional[bool] = None,
    flag_color: Optional[str] = None,
    read_status: str = "all",
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include_content: bool = False,
    max_content_length: int = 500,
    body_text: Optional[str] = None,
    max_results: Optional[int] = 20,
    output_format: str = "text",
    offset: int = 0,
    limit: Optional[int] = None,
    sort: str = "date_desc",
) -> str:
    """
    Unified search tool with JSON output, pagination, and real date filtering.

    Consolidates subject search, sender search, body content search, and
    cross-account search into a single tool.

    Args:
        account: Account name to search in (e.g., "Gmail", "Work").
            If None, searches ALL accounts (slower).
        mailbox: Mailbox to search (default: "INBOX", use "All" for all mailboxes, or specific folder name)
        subject_keyword: Optional keyword to search in subject
        subject_keywords: Optional list of subject keywords; matches any keyword
        sender: Optional sender email or name to filter by
        has_attachments: Optional filter for emails with attachments (True/False/None)
        flagged: Optional filter for flagged emails (True/False/None)
        flag_color: Optional filter for a specific flag color: "red", "orange",
            "yellow", "green", "blue", "purple", or "gray". Implies flagged=True.
        read_status: Filter by read status: "all", "read", "unread" (default: "all")
        date_from: Optional start date filter (format: "YYYY-MM-DD")
        date_to: Optional end date filter (format: "YYYY-MM-DD")
        include_content: Whether to include email content preview (slower)
        max_content_length: Maximum content length in characters when include_content=True (default: 500, 0 = unlimited)
        body_text: Optional text to search for in email body content (case-insensitive).
            WARNING: body search is significantly slower as it reads each message body.
        max_results: Backward-compatible alias for limit
        output_format: Output format: "text" or "json" (default: "text")
        offset: Number of matching results to skip before returning data
        limit: Maximum number of results to return per page
        sort: Result sort order: "date_desc" or "date_asc"

    Returns:
        Formatted list of matching emails or JSON payload with stable message metadata
    """
    if output_format not in {"text", "json"}:
        return "Error: Invalid output_format. Use: text, json"

    if limit is None:
        limit = max_results if max_results is not None else 100

    subject_terms = normalize_search_terms(subject_keyword, subject_keywords)

    try:
        records, scan_limited = _search_mail_records(
            account=account,
            mailbox=mailbox,
            subject_terms=subject_terms,
            sender=sender,
            has_attachments=has_attachments,
            flagged=flagged,
            flag_color=flag_color,
            read_status=read_status,
            date_from=date_from,
            date_to=date_to,
            include_content=include_content,
            content_length=max_content_length,
            offset=offset,
            limit=limit,
            sort=sort,
            body_text=body_text,
        )
        return _build_search_response(
            records,
            offset=offset,
            limit=limit,
            sort=sort,
            output_format=output_format,
            subject_only=False,
            scan_limited=scan_limited,
        )
    except ValueError as exc:
        return f"Error: {exc}"


_THREAD_PREFIX_RE = re.compile(r"^\s*(re|aw|fwd|fw|wg)\s*(\[\d+\])?\s*:\s*", re.IGNORECASE)
_MESSAGE_ID_RE = re.compile(r"<([^<>@\s]+@[^<>\s]+)>")
_REFERENCE_HEADER_RE = re.compile(
    r"^(?:references|in-reply-to)\s*:(.*?)(?=^\S|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def _strip_thread_prefixes(subject: str) -> str:
    """Strip repeated Re:/AW:/Fwd:/WG: prefixes from a subject."""
    cleaned = (subject or "").strip()
    while True:
        stripped = _THREAD_PREFIX_RE.sub("", cleaned, count=1)
        if stripped == cleaned:
            return cleaned.strip()
        cleaned = stripped.strip()


def _referenced_ids(headers: str) -> set:
    """Return every Message-ID referenced by In-Reply-To / References."""
    found = set()
    for block in _REFERENCE_HEADER_RE.findall(headers or ""):
        found.update(_MESSAGE_ID_RE.findall(block))
    return found


def _collect_thread_candidates(
    account: str, mailbox: str, cleaned_keyword: str, max_messages: int
):
    """Return (records, error) for messages whose subject carries the keyword.

    Each record holds the fields needed to display a message plus the raw
    headers used to work out who is actually talking to whom.
    """
    separator = "<<<AMMCP-" + os.urandom(8).hex() + ">>>"
    row_separator = "<<<AMMCPROW-" + os.urandom(8).hex() + ">>>"
    safe_account = escape_applescript(account)
    safe_keyword = escape_applescript(cleaned_keyword)
    safe_mailbox = escape_applescript(mailbox)

    if mailbox == "All":
        mailbox_setup = "set searchMailboxes to every mailbox of targetAccount"
    else:
        mailbox_setup = f'''try
                set searchMailboxes to {{mailbox "{safe_mailbox}" of targetAccount}}
            on error
                if "{safe_mailbox}" is "INBOX" then
                    set searchMailboxes to {{mailbox "Inbox" of targetAccount}}
                else
                    error "Mailbox not found: {safe_mailbox}"
                end if
            end try'''

    script = f'''
    tell application "Mail"
        with timeout of 300 seconds
            try
                set targetAccount to account "{safe_account}"
                {mailbox_setup}

                set collected to 0
                set outputText to ""

                repeat with currentMailbox in searchMailboxes
                    if collected >= {int(max_messages)} then exit repeat
                    try
                        -- `whose` filters inside Mail; walking every message
                        -- over Apple Events takes minutes on a big archive.
                        set candidates to (messages of currentMailbox whose subject contains "{safe_keyword}")
                        repeat with aMessage in candidates
                            if collected >= {int(max_messages)} then exit repeat
                            try
                                set headerText to ""
                                try
                                    set headerText to all headers of aMessage
                                end try
                                set messageIdText to ""
                                try
                                    set messageIdText to message id of aMessage
                                end try

                                set previewText to ""
                                try
                                    set rawContent to content of aMessage
                                    set AppleScript's text item delimiters to {{return, linefeed, tab}}
                                    set contentParts to text items of rawContent
                                    set AppleScript's text item delimiters to " "
                                    set previewText to contentParts as string
                                    set AppleScript's text item delimiters to ""
                                    if length of previewText > 150 then
                                        set previewText to text 1 thru 150 of previewText & "..."
                                    end if
                                end try

                                set outputText to outputText & messageIdText & "{separator}" & ¬
                                    (subject of aMessage) & "{separator}" & ¬
                                    (sender of aMessage) & "{separator}" & ¬
                                    ((date received of aMessage) as string) & "{separator}" & ¬
                                    (((date received of aMessage) - (current date)) as text) & "{separator}" & ¬
                                    ((read status of aMessage) as text) & "{separator}" & ¬
                                    (name of currentMailbox) & "{separator}" & ¬
                                    previewText & "{separator}" & ¬
                                    headerText & "{row_separator}"
                                set collected to collected + 1
                            end try
                        end repeat
                    end try
                end repeat

                return outputText
            on error errMsg
                return "ERROR{separator}" & errMsg
            end try
        end timeout
    end tell
    '''

    raw = run_applescript(script, timeout=300) or ""
    if raw.startswith("ERROR" + separator):
        return [], "Error: " + raw.split(separator, 1)[1].strip()

    records = []
    for row in raw.split(row_separator):
        if not row.strip():
            continue
        parts = row.split(separator)
        if len(parts) < 9:
            continue
        try:
            age_seconds = int(float(parts[4].strip()))
        except ValueError:
            age_seconds = 0
        records.append(
            {
                "message_id": parts[0].strip().strip("<>"),
                "subject": parts[1].strip(),
                "sender": parts[2].strip(),
                "date": parts[3].strip(),
                "age_seconds": age_seconds,
                "read": parts[5].strip().lower() == "true",
                "mailbox": parts[6].strip(),
                "preview": parts[7].strip(),
                "references": _referenced_ids(separator.join(parts[8:])),
            }
        )
    records.sort(key=lambda record: record["age_seconds"])
    return records, None


def _split_thread_by_linkage(records: List[Dict[str, Any]]):
    """Split candidates into the real thread and same-subject strangers.

    The newest candidate seeds the thread; everything reachable from it
    through Message-ID references belongs to it. A message without any
    Message-ID at all stays in the thread, since dropping it would hide mail that
    the old subject-only grouping did show.
    """
    if not records:
        return [], []

    # Sort here rather than trusting the caller: the seed must be the newest
    # message, otherwise the thread is grown from the wrong end.
    records = sorted(records, key=lambda record: record["age_seconds"])
    seed = records[-1]
    thread_ids = set()
    if seed["message_id"]:
        thread_ids.add(seed["message_id"])
    thread_ids.update(seed["references"])

    # Repeat until nothing new joins: a reply can reference a message that
    # itself only joins the thread later in the list.
    linked = [seed]
    remaining = [record for record in records if record is not seed]
    changed = True
    while changed:
        changed = False
        for record in list(remaining):
            own_id = record["message_id"]
            if (own_id and own_id in thread_ids) or (record["references"] & thread_ids):
                linked.append(record)
                remaining.remove(record)
                if own_id:
                    thread_ids.add(own_id)
                thread_ids.update(record["references"])
                changed = True

    for record in list(remaining):
        if not record["message_id"] and not record["references"]:
            linked.append(record)
            remaining.remove(record)

    linked.sort(key=lambda record: record["age_seconds"])
    remaining.sort(key=lambda record: record["age_seconds"])
    return linked, remaining


def _format_thread_record(record: Dict[str, Any]) -> List[str]:
    indicator = "✓" if record["read"] else "✉"
    lines = [
        f"{indicator} {record['subject']}",
        f"   From: {record['sender']}",
        f"   Date: {record['date']}",
    ]
    if record["mailbox"]:
        lines.append(f"   Mailbox: {record['mailbox']}")
    if record["preview"]:
        lines.append(f"   Preview: {record['preview']}")
    lines.append("")
    return lines


@mcp.tool()
@inject_preferences
def get_email_thread(
    account: str, subject_keyword: str, mailbox: str = "INBOX", max_messages: int = 50
) -> str:
    """
    Get an email conversation thread - messages linked by Message-ID, not just by subject.

    Candidates are found by subject (fast, filtered inside Mail), then linked
    through their Message-ID / In-Reply-To / References headers. Messages that
    merely share the subject but belong to a different conversation are listed
    separately instead of being mixed into the thread.

    Args:
        account: Account name (e.g., "Gmail", "Work")
        subject_keyword: Keyword to identify the thread (e.g., "Re: Project Update")
        mailbox: Mailbox to search in (default: "INBOX", use "All" for all mailboxes)
        max_messages: Maximum number of candidate messages to inspect (default: 50).
            The cap applies before linking: only the first N subject matches
            that Mail returns are considered, so the thread is seeded from the
            newest of those N, not necessarily the newest message overall.
            Raise it for long-running subjects.

    Returns:
        Formatted thread view sorted oldest first, with unlinked same-subject
        messages called out separately
    """
    cleaned_keyword = _strip_thread_prefixes(subject_keyword)
    if not cleaned_keyword:
        return "Error: 'subject_keyword' is empty after stripping Re:/Fwd: prefixes"

    records, error = _collect_thread_candidates(
        account, mailbox, cleaned_keyword, max_messages
    )
    if error:
        return error
    if not records:
        return (
            f"EMAIL THREAD VIEW\n\nThread topic: {cleaned_keyword}\n"
            f"Account: {account}\n\nNo message found matching: {subject_keyword} "
            f"(searched mailbox: {mailbox})"
        )

    linked, unlinked = _split_thread_by_linkage(records)

    lines = [
        "EMAIL THREAD VIEW",
        "",
        f"Thread topic: {cleaned_keyword}",
        f"Account: {account}",
        "",
        "━" * 40,
        f"FOUND {len(linked)} MESSAGE(S) IN THREAD",
        "━" * 40,
        "",
    ]
    for record in linked:
        lines.extend(_format_thread_record(record))

    if unlinked:
        lines.append("━" * 40)
        lines.append(
            f"{len(unlinked)} MESSAGE(S) WITH THE SAME SUBJECT BUT NO LINK TO THIS THREAD"
        )
        lines.append("━" * 40)
        lines.append("")
        for record in unlinked:
            lines.extend(_format_thread_record(record))

    return "\n".join(lines).rstrip() + "\n"


import contextlib
import json
import logging
from typing import Any
from apple_mail_mcp import imap as imap_backend
from apple_mail_mcp.constants import FLAG_COLOR_NAMES, SKIP_FOLDERS
from apple_mail_mcp.core import (
    LOWERCASE_HANDLER,
    escape_applescript,
    inject_preferences,
    read_flag_index_script,
    resolve_flag_color,
    run_applescript,
)
from apple_mail_mcp.server import mcp

_log = logging.getLogger("apple-mail-mcp.search")


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
        skipped = 0

        for folder in folders:
            try:
                conn.select(f'"{folder}"', readonly=True)
            except Exception:
                continue

            uids = imap_backend.imap_search(conn, criteria)
            if not uids:
                continue

            if offset and skipped + len(uids) <= offset:
                skipped += len(uids)
                continue

            folder_offset = max(offset - skipped, 0)
            remaining = max_results - len(all_results)
            selected = uids[folder_offset : folder_offset + remaining]
            skipped += folder_offset
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


def _search_emails_json(
    account: str,
    mailbox: str,
    subject_keyword: str | None,
    sender: str | None,
    has_attachments: bool | None,
    read_status: str,
    max_results: int,
    flag_color: str | None = None,
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
    flag_index: int | None = None
    if flag_color is not None:
        try:
            flag_index = resolve_flag_color(flag_color)
        except ValueError as exc:
            return json.dumps({"error": str(exc)}, indent=2)
        conditions.append(f"(messageFlagIndex is {flag_index})")
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
                                {read_flag_index_script()}
                                if {condition_str} then
                                    set end of resultLines to messageSubject & "|||" & messageSender & "|||" & (messageDate as string) & "|||" & messageRead & "|||" & "{escaped_account}" & "|||" & mailboxName & "|||" & messageFlagIndex
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
                if len(parts) > 6:
                    try:
                        flag_index_value = int(parts[6].strip())
                    except ValueError:
                        flag_index_value = -1
                    if flag_index_value in FLAG_COLOR_NAMES:
                        emails[-1]["flag_color"] = FLAG_COLOR_NAMES[flag_index_value]
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

