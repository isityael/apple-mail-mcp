"""Management tools: moving, status updates, trash, and attachments."""

import os
import re

from apple_mail_mcp.core import (
    build_filter_condition,
    build_mailbox_ref,
    equals_any_numeric_condition,
    escape_applescript,
    inbox_mailbox_script,
    inject_preferences,
    normalize_message_ids,
    resolve_flag_color,
    run_applescript,
)
from apple_mail_mcp.server import mcp


@mcp.tool()
@inject_preferences
def move_email(
    account: str,
    subject_keyword: str | None,
    to_mailbox: str,
    from_mailbox: str = "INBOX",
    max_moves: int = 1,
    message_ids: list[str] | None = None,
) -> str:
    """
    Move email(s) matching a subject keyword from one mailbox to another.

    Args:
        account: Account name (e.g., "Gmail", "Work")
        subject_keyword: Keyword to search for in email subjects
        to_mailbox: Destination mailbox name. For nested mailboxes, use "/" separator (e.g., "Projects/Amplify Impact")
        from_mailbox: Source mailbox name (default: "INBOX")
        max_moves: Maximum number of emails to move (default: 1, safety limit)
        message_ids: Exact Apple Mail message ids to move; when provided, subject filtering is ignored

    Returns:
        Confirmation message with details of moved emails
    """

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)
    safe_subject_keyword = escape_applescript(subject_keyword) if subject_keyword else ""
    safe_from_mailbox = escape_applescript(from_mailbox)
    safe_to_mailbox = escape_applescript(to_mailbox)

    # Parse nested mailbox path
    mailbox_parts = to_mailbox.split("/")

    # Build the nested mailbox reference
    if len(mailbox_parts) > 1:
        # Nested mailbox
        dest_mailbox_script = f'mailbox "{escape_applescript(mailbox_parts[-1])}" of '
        for i in range(len(mailbox_parts) - 2, -1, -1):
            dest_mailbox_script += f'mailbox "{escape_applescript(mailbox_parts[i])}" of '
        dest_mailbox_script += "targetAccount"
    else:
        dest_mailbox_script = f'mailbox "{safe_to_mailbox}" of targetAccount'

    if message_ids is not None:
        normalized_ids = normalize_message_ids(message_ids)
        if not normalized_ids:
            return "Error: 'message_ids' must contain one or more numeric Mail ids"
        id_condition = equals_any_numeric_condition("id", normalized_ids)
        script = f'''
        tell application "Mail"
            set outputText to "MOVING EMAILS BY IDS" & return & return
            set movedCount to 0

            try
                set targetAccount to account "{safe_account}"
                {build_mailbox_ref(from_mailbox, var_name="sourceMailbox")}
                set destMailbox to {dest_mailbox_script}
                set sourceMessages to every message of sourceMailbox whose {id_condition}

                repeat with aMessage in sourceMessages
                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage
                        move aMessage to destMailbox
                        set outputText to outputText & "✓ Moved: " & messageSubject & return
                        set outputText to outputText & "  From: " & messageSender & return
                        set outputText to outputText & "  Date: " & (messageDate as string) & return
                        set outputText to outputText & "  {safe_from_mailbox} → {safe_to_mailbox}" & return & return
                        set movedCount to movedCount + 1
                    end try
                end repeat

                set outputText to outputText & "========================================" & return
                set outputText to outputText & "REQUESTED: {len(normalized_ids)} id(s), MOVED: " & movedCount & return
                set outputText to outputText & "========================================" & return

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''
        return run_applescript(script)

    if not subject_keyword:
        return "Error: subject_keyword is required unless message_ids is provided"

    script = f'''
    tell application "Mail"
        set outputText to "MOVING EMAILS" & return & return
        set movedCount to 0

        try
            set targetAccount to account "{safe_account}"
            -- Try to get source mailbox (handle both "INBOX"/"Inbox" variations)
            try
                set sourceMailbox to mailbox "{safe_from_mailbox}" of targetAccount
            on error
                if "{safe_from_mailbox}" is "INBOX" then
                    set sourceMailbox to mailbox "Inbox" of targetAccount
                else
                    error "Source mailbox not found"
                end if
            end try

            -- Get destination mailbox (handles nested mailboxes)
            set destMailbox to {dest_mailbox_script}
            set sourceMessages to every message of sourceMailbox

            repeat with aMessage in sourceMessages
                if movedCount >= {max_moves} then exit repeat

                try
                    set messageSubject to subject of aMessage

                    -- Check if subject contains keyword (case insensitive)
                    if messageSubject contains "{safe_subject_keyword}" then
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage

                        -- Move the message
                        move aMessage to destMailbox

                        set outputText to outputText & "✓ Moved: " & messageSubject & return
                        set outputText to outputText & "  From: " & messageSender & return
                        set outputText to outputText & "  Date: " & (messageDate as string) & return
                        set outputText to outputText & "  {safe_from_mailbox} → {safe_to_mailbox}" & return & return

                        set movedCount to movedCount + 1
                    end if
                end try
            end repeat

            set outputText to outputText & "========================================" & return
            set outputText to outputText & "TOTAL MOVED: " & movedCount & " email(s)" & return
            set outputText to outputText & "========================================" & return

        on error errMsg
            return "Error: " & errMsg & return & "Please check that account and mailbox names are correct. For nested mailboxes, use '/' separator (e.g., 'Projects/Amplify Impact')."
        end try

        return outputText
    end tell
    '''

    result = run_applescript(script)
    return result


@mcp.tool()
@inject_preferences
def save_email_attachment(account: str, subject_keyword: str, attachment_name: str, save_path: str) -> str:
    """
    Save a specific attachment from an email to disk.

    Args:
        account: Account name (e.g., "Gmail", "Work", "Personal")
        subject_keyword: Keyword to search for in email subjects
        attachment_name: Name of the attachment to save
        save_path: Full path where to save the attachment

    Returns:
        Confirmation message with save location
    """

    # Expand tilde in save_path (POSIX file in AppleScript does not expand ~)
    expanded_path = os.path.expanduser(save_path)

    # Path validation: resolve to absolute path and enforce safety constraints
    resolved_path = os.path.realpath(expanded_path)
    home_dir = os.path.expanduser("~")

    # Must be under the user's home directory
    if not resolved_path.startswith(home_dir + os.sep) and resolved_path != home_dir:
        return f"Error: Save path must be under your home directory ({home_dir}). Got: {resolved_path}"

    # Block sensitive directories
    sensitive_dirs = [
        os.path.join(home_dir, ".ssh"),
        os.path.join(home_dir, ".gnupg"),
        os.path.join(home_dir, ".config"),
        os.path.join(home_dir, ".aws"),
        os.path.join(home_dir, ".claude"),
        os.path.join(home_dir, "Library", "LaunchAgents"),
        os.path.join(home_dir, "Library", "LaunchDaemons"),
        os.path.join(home_dir, "Library", "Keychains"),
    ]
    for sensitive_dir in sensitive_dirs:
        if resolved_path.startswith(sensitive_dir + os.sep) or resolved_path == sensitive_dir:
            return f"Error: Cannot save attachments to sensitive directory: {sensitive_dir}"

    expanded_path = resolved_path

    # Escape for AppleScript
    escaped_account = escape_applescript(account)
    escaped_keyword = escape_applescript(subject_keyword)
    escaped_attachment = escape_applescript(attachment_name)
    escaped_path = escape_applescript(expanded_path)

    script = f'''
    tell application "Mail"
        set outputText to ""

        try
            set targetAccount to account "{escaped_account}"
            {inbox_mailbox_script("inboxMailbox", "targetAccount")}
            set inboxMessages to every message of inboxMailbox
            set foundAttachment to false

            repeat with aMessage in inboxMessages
                try
                    set messageSubject to subject of aMessage

                    -- Check if subject contains keyword
                    if messageSubject contains "{escaped_keyword}" then
                        set msgAttachments to mail attachments of aMessage

                        repeat with anAttachment in msgAttachments
                            set attachmentFileName to name of anAttachment

                            if attachmentFileName contains "{escaped_attachment}" then
                                -- Save the attachment
                                save anAttachment in POSIX file "{escaped_path}"

                                set outputText to "✓ Attachment saved successfully!" & return & return
                                set outputText to outputText & "Email: " & messageSubject & return
                                set outputText to outputText & "Attachment: " & attachmentFileName & return
                                set outputText to outputText & "Saved to: {escaped_path}" & return

                                set foundAttachment to true
                                exit repeat
                            end if
                        end repeat

                        if foundAttachment then exit repeat
                    end if
                end try
            end repeat

            if not foundAttachment then
                set outputText to "⚠ Attachment not found" & return
                set outputText to outputText & "Email keyword: {escaped_keyword}" & return
                set outputText to outputText & "Attachment name: {escaped_attachment}" & return
            end if

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''

    result = run_applescript(script)
    return result


@mcp.tool()
@inject_preferences
def update_email_status(
    account: str,
    action: str,
    subject_keyword: str | None = None,
    sender: str | None = None,
    mailbox: str = "INBOX",
    max_updates: int = 10,
    apply_to_all: bool = False,
    message_ids: list[str] | None = None,
    flag_color: str | None = None,
) -> str:
    """
    Update email status - mark as read/unread or flag/unflag emails.

    Args:
        account: Account name (e.g., "Gmail", "Work")
        action: Action to perform: "mark_read", "mark_unread", "flag", "unflag"
        subject_keyword: Optional keyword to filter emails by subject
        sender: Optional sender to filter emails by
        mailbox: Mailbox to search in (default: "INBOX")
        max_updates: Maximum number of emails to update (safety limit, default: 10)
        apply_to_all: Must be True to allow updates without subject_keyword or sender filter
        message_ids: Exact Apple Mail message ids to update; when provided, filters are ignored
        flag_color: Optional flag color for action="flag": red, orange, yellow, green, blue, purple, gray

    Returns:
        Confirmation message with details of updated emails
    """

    # Safety check: require at least one filter or explicit apply_to_all
    if message_ids is None and not subject_keyword and not sender and not apply_to_all:
        return (
            "Error: No filter provided. Provide subject_keyword or sender to filter emails, "
            "or set apply_to_all=True to update all messages in the mailbox."
        )

    flag_index: int | None = None
    if flag_color is not None:
        if action != "flag":
            return "Error: 'flag_color' is only valid with action='flag'"
        try:
            flag_index = resolve_flag_color(flag_color)
        except ValueError as exc:
            return f"Error: {exc}"

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)

    # Build search condition using helper
    condition_str = build_filter_condition(subject=subject_keyword, sender=sender)

    # Build action script
    if action == "mark_read":
        action_script = "set read status of aMessage to true"
        action_label = "Marked as read"
    elif action == "mark_unread":
        action_script = "set read status of aMessage to false"
        action_label = "Marked as unread"
    elif action == "flag":
        if flag_index is None:
            action_script = "set flagged status of aMessage to true"
            action_label = "Flagged"
        else:
            action_script = f"set flagged status of aMessage to true\n                        set flag index of aMessage to {flag_index}"
            action_label = f"Flagged ({flag_color.strip().lower()})"
    elif action == "unflag":
        action_script = "set flagged status of aMessage to false"
        action_label = "Unflagged"
    else:
        return f"Error: Invalid action '{action}'. Use: mark_read, mark_unread, flag, unflag"

    if message_ids is not None:
        normalized_ids = normalize_message_ids(message_ids)
        if not normalized_ids:
            return "Error: 'message_ids' must contain one or more numeric Mail ids"
        id_condition = equals_any_numeric_condition("id", normalized_ids)
        script = f'''
        tell application "Mail"
            set outputText to "UPDATING EMAIL STATUS BY IDS: {action_label}" & return & return
            set updateCount to 0

            try
                set targetAccount to account "{safe_account}"
                {build_mailbox_ref(mailbox, var_name="targetMailbox")}
                set mailboxMessages to every message of targetMailbox whose {id_condition}

                repeat with aMessage in mailboxMessages
                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage
                        {action_script}
                        set outputText to outputText & "✓ {action_label}: " & messageSubject & return
                        set outputText to outputText & "   From: " & messageSender & return
                        set outputText to outputText & "   Date: " & (messageDate as string) & return & return
                        set updateCount to updateCount + 1
                    end try
                end repeat

                set outputText to outputText & "========================================" & return
                set outputText to outputText & "REQUESTED: {len(normalized_ids)} id(s), UPDATED: " & updateCount & return
                set outputText to outputText & "========================================" & return

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''
        return run_applescript(script)

    script = f'''
    tell application "Mail"
        set outputText to "UPDATING EMAIL STATUS: {action_label}" & return & return
        set updateCount to 0

        try
            set targetAccount to account "{safe_account}"
            {build_mailbox_ref(mailbox, var_name="targetMailbox")}

            set mailboxMessages to every message of targetMailbox

            repeat with aMessage in mailboxMessages
                if updateCount >= {max_updates} then exit repeat

                try
                    set messageSubject to subject of aMessage
                    set messageSender to sender of aMessage
                    set messageDate to date received of aMessage

                    -- Apply filter conditions
                    if {condition_str} then
                        {action_script}

                        set outputText to outputText & "✓ {action_label}: " & messageSubject & return
                        set outputText to outputText & "   From: " & messageSender & return
                        set outputText to outputText & "   Date: " & (messageDate as string) & return & return

                        set updateCount to updateCount + 1
                    end if
                end try
            end repeat

            set outputText to outputText & "========================================" & return
            set outputText to outputText & "TOTAL UPDATED: " & updateCount & " email(s)" & return
            set outputText to outputText & "========================================" & return

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''

    result = run_applescript(script)
    return result


@mcp.tool()
@inject_preferences
def manage_trash(
    account: str,
    action: str,
    subject_keyword: str | None = None,
    sender: str | None = None,
    mailbox: str = "INBOX",
    max_deletes: int = 5,
    confirm_empty: bool = False,
    apply_to_all: bool = False,
) -> str:
    """
    Manage trash operations - delete emails or empty trash.

    Args:
        account: Account name (e.g., "Gmail", "Work")
        action: Action to perform: "move_to_trash", "delete_permanent", "empty_trash"
        subject_keyword: Optional keyword to filter emails (not used for empty_trash)
        sender: Optional sender to filter emails (not used for empty_trash)
        mailbox: Source mailbox (default: "INBOX", not used for empty_trash or delete_permanent)
        max_deletes: Maximum number of emails to delete (safety limit, default: 5)
        confirm_empty: Must be True to execute "empty_trash" action (safety confirmation)
        apply_to_all: Must be True to allow operations without subject_keyword or sender filter

    Returns:
        Confirmation message with details of deleted emails
    """

    # Escape all user inputs for AppleScript
    safe_account = escape_applescript(account)

    if action == "empty_trash":
        if not confirm_empty:
            return (
                "Error: empty_trash permanently deletes ALL messages in the trash. Set confirm_empty=True to proceed."
            )
        script = f'''
        tell application "Mail"
            set outputText to "EMPTYING TRASH" & return & return

            try
                set targetAccount to account "{safe_account}"
                set trashMailbox to mailbox "Trash" of targetAccount
                set trashMessages to every message of trashMailbox
                set messageCount to count of trashMessages
                set deleteCount to 0

                -- Delete messages in trash, respecting max_deletes
                repeat with aMessage in trashMessages
                    if deleteCount >= {max_deletes} then exit repeat
                    delete aMessage
                    set deleteCount to deleteCount + 1
                end repeat

                set outputText to outputText & "✓ Emptied trash for account: {safe_account}" & return
                set outputText to outputText & "   Deleted " & deleteCount & " of " & messageCount & " message(s)" & return
                if deleteCount < messageCount then
                    set outputText to outputText & "   (limited by max_deletes=" & {max_deletes} & ")" & return
                end if

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''
    elif action == "delete_permanent":
        # Safety check: require at least one filter or explicit apply_to_all
        if not subject_keyword and not sender and not apply_to_all:
            return (
                "Error: No filter provided. Provide subject_keyword or sender to filter emails, "
                "or set apply_to_all=True to delete all matching messages."
            )

        condition_str = build_filter_condition(subject=subject_keyword, sender=sender)

        script = f'''
        tell application "Mail"
            set outputText to "PERMANENTLY DELETING EMAILS" & return & return
            set deleteCount to 0

            try
                set targetAccount to account "{safe_account}"
                set trashMailbox to mailbox "Trash" of targetAccount
                set trashMessages to every message of trashMailbox

                repeat with aMessage in trashMessages
                    if deleteCount >= {max_deletes} then exit repeat

                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage

                        -- Apply filter conditions
                        if {condition_str} then
                            set outputText to outputText & "✓ Permanently deleted: " & messageSubject & return
                            set outputText to outputText & "   From: " & messageSender & return & return

                            delete aMessage
                            set deleteCount to deleteCount + 1
                        end if
                    end try
                end repeat

                set outputText to outputText & "========================================" & return
                set outputText to outputText & "TOTAL DELETED: " & deleteCount & " email(s)" & return
                set outputText to outputText & "========================================" & return

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''
    else:  # move_to_trash
        # Safety check: require at least one filter or explicit apply_to_all
        if not subject_keyword and not sender and not apply_to_all:
            return (
                "Error: No filter provided. Provide subject_keyword or sender to filter emails, "
                "or set apply_to_all=True to move all messages to trash."
            )

        condition_str = build_filter_condition(subject=subject_keyword, sender=sender)
        safe_mailbox = escape_applescript(mailbox)

        script = f'''
        tell application "Mail"
            set outputText to "MOVING EMAILS TO TRASH" & return & return
            set deleteCount to 0

            try
                set targetAccount to account "{safe_account}"
                -- Get source mailbox
                try
                    set sourceMailbox to mailbox "{safe_mailbox}" of targetAccount
                on error
                    if "{safe_mailbox}" is "INBOX" then
                        set sourceMailbox to mailbox "Inbox" of targetAccount
                    else
                        error "Mailbox not found: {safe_mailbox}"
                    end if
                end try

                -- Get trash mailbox
                set trashMailbox to mailbox "Trash" of targetAccount
                set sourceMessages to every message of sourceMailbox

                repeat with aMessage in sourceMessages
                    if deleteCount >= {max_deletes} then exit repeat

                    try
                        set messageSubject to subject of aMessage
                        set messageSender to sender of aMessage
                        set messageDate to date received of aMessage

                        -- Apply filter conditions
                        if {condition_str} then
                            move aMessage to trashMailbox

                            set outputText to outputText & "✓ Moved to trash: " & messageSubject & return
                            set outputText to outputText & "   From: " & messageSender & return
                            set outputText to outputText & "   Date: " & (messageDate as string) & return & return

                            set deleteCount to deleteCount + 1
                        end if
                    end try
                end repeat

                set outputText to outputText & "========================================" & return
                set outputText to outputText & "TOTAL MOVED TO TRASH: " & deleteCount & " email(s)" & return
                set outputText to outputText & "========================================" & return

            on error errMsg
                return "Error: " & errMsg
            end try

            return outputText
        end tell
        '''

    result = run_applescript(script)
    return result


# Characters that could break AppleScript strings or mailbox names
_INVALID_MAILBOX_CHARS = re.compile(r"[\\\"<>|?*:\x00-\x1f]")


@mcp.tool()
@inject_preferences
def create_mailbox(
    account: str,
    name: str,
    parent_mailbox: str | None = None,
) -> str:
    """
    Create a new mailbox (folder) in the specified account.

    Supports nested paths via the parent_mailbox parameter (e.g.,
    parent_mailbox="Projects" + name="2024" creates Projects/2024).
    You can also pass a full slash-separated path as *name*
    (e.g., "Projects/2024/ClientName") and omit parent_mailbox.

    Args:
        account: Account name (e.g., "Gmail", "Work")
        name: Name for the new mailbox. May contain "/" to create a
              nested path in one call (each segment is created if needed).
        parent_mailbox: Optional existing parent folder for nesting.

    Returns:
        Confirmation with the new mailbox path.
    """
    # Validate name
    if not name or not name.strip():
        return "Error: Mailbox name cannot be empty."

    # Split name into segments (support "A/B/C" shorthand)
    segments = [s.strip() for s in name.split("/") if s.strip()]
    if not segments:
        return "Error: Mailbox name cannot be empty."

    for seg in segments:
        if _INVALID_MAILBOX_CHARS.search(seg):
            return (
                f"Error: Invalid characters in mailbox name segment '{seg}'. "
                'Avoid \\ " < > | ? * : and control characters.'
            )

    safe_account = escape_applescript(account)

    # If parent_mailbox is given, prepend its segments
    if parent_mailbox:
        parent_segments = [s.strip() for s in parent_mailbox.split("/") if s.strip()]
        segments = parent_segments + segments

    # Build AppleScript to create each level one at a time
    create_blocks = ""
    for depth in range(len(segments)):
        seg = escape_applescript(segments[depth])
        if depth == 0:
            create_blocks += f'''
            try
                set parentRef to mailbox "{seg}" of targetAccount
            on error
                make new mailbox at targetAccount with properties {{name:"{seg}"}}
                set parentRef to mailbox "{seg}" of targetAccount
            end try
'''
        else:
            create_blocks += f'''
            try
                set parentRef to mailbox "{seg}" of parentRef
            on error
                make new mailbox at parentRef with properties {{name:"{seg}"}}
                set parentRef to mailbox "{seg}" of parentRef
            end try
'''

    full_path = "/".join(segments)
    safe_path = escape_applescript(full_path)

    script = f'''
    tell application "Mail"
        set outputText to "CREATING MAILBOX" & return & return

        try
            set targetAccount to account "{safe_account}"

            {create_blocks}

            set outputText to outputText & "OK Mailbox created successfully!" & return & return
            set outputText to outputText & "Account: {safe_account}" & return
            set outputText to outputText & "Path: {safe_path}" & return

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''

    return run_applescript(script)


@mcp.tool()
@inject_preferences
def archive_emails(
    account: str,
    subject_keyword: str | None = None,
    sender: str | None = None,
    older_than_days: int | None = None,
    only_read: bool = True,
    from_mailbox: str = "INBOX",
    archive_mailbox: str = "Archive",
    max_archive: int = 50,
    dry_run: bool = True,
) -> str:
    """
    Archive emails matching criteria by moving them to an Archive mailbox.

    Safety features:
    - At least one filter (subject_keyword, sender, or older_than_days) is required.
    - dry_run=True (default) previews what would be archived without moving.
    - only_read=True (default) skips unread emails.
    - max_archive caps the number of emails moved in one call.

    Args:
        account: Account name (e.g., "Gmail", "Work")
        subject_keyword: Optional keyword to filter by subject
        sender: Optional sender to filter by
        older_than_days: Optional age filter - only archive emails older than N days
        only_read: If True (default), only archive emails that have been read
        from_mailbox: Source mailbox (default "INBOX")
        archive_mailbox: Destination mailbox (default "Archive")
        max_archive: Maximum emails to archive per call (default 50)
        dry_run: If True (default), only preview - do not actually move emails

    Returns:
        Summary of archived (or previewed) emails
    """
    # Safety: require at least one filter
    if not subject_keyword and not sender and not older_than_days:
        return (
            "Error: At least one filter is required (subject_keyword, sender, "
            "or older_than_days). This prevents accidentally archiving everything."
        )

    safe_account = escape_applescript(account)

    # Build conditions
    condition_str = build_filter_condition(subject=subject_keyword, sender=sender)
    if only_read:
        read_cond = "messageRead is true"
        condition_str = f"{condition_str} and {read_cond}" if condition_str != "true" else read_cond

    # Date filter
    date_setup = ""
    date_cond = ""
    if older_than_days and older_than_days > 0:
        date_setup = f"set cutoffDate to (current date) - ({older_than_days} * days)"
        date_cond = " and messageDate < cutoffDate"

    if dry_run:
        action_label = "DRY RUN - PREVIEW ARCHIVE"
        move_script = ""
        result_prefix = "Would archive"
    else:
        action_label = "ARCHIVING EMAILS"
        move_script = "move aMessage to destMailbox"
        result_prefix = "Archived"

    script = f'''
    tell application "Mail"
        set outputText to "{action_label}" & return & return
        set archiveCount to 0

        try
            set targetAccount to account "{safe_account}"

            -- Resolve source mailbox
            {build_mailbox_ref(from_mailbox, var_name="sourceMailbox")}

            -- Resolve or create archive mailbox
            try
                set destMailbox to mailbox "{escape_applescript(archive_mailbox)}" of targetAccount
            on error
                make new mailbox at targetAccount with properties {{name:"{escape_applescript(archive_mailbox)}"}}
                set destMailbox to mailbox "{escape_applescript(archive_mailbox)}" of targetAccount
            end try

            {date_setup}

            set sourceMessages to every message of sourceMailbox

            repeat with aMessage in sourceMessages
                if archiveCount >= {max_archive} then exit repeat

                try
                    set messageSubject to subject of aMessage
                    set messageSender to sender of aMessage
                    set messageDate to date received of aMessage
                    set messageRead to read status of aMessage

                    if {condition_str}{date_cond} then
                        {move_script}

                        set outputText to outputText & "{result_prefix}: " & messageSubject & return
                        set outputText to outputText & "   From: " & messageSender & return
                        set outputText to outputText & "   Date: " & (messageDate as string) & return & return

                        set archiveCount to archiveCount + 1
                    end if
                end try
            end repeat

            set outputText to outputText & "========================================" & return
            set outputText to outputText & "TOTAL: " & archiveCount & " email(s) {result_prefix.lower()}" & return
            set outputText to outputText & "========================================" & return

        on error errMsg
            return "Error: " & errMsg
        end try

        return outputText
    end tell
    '''

    return run_applescript(script)


@mcp.tool()
@inject_preferences
def synchronize_account(account: str | None = None) -> str:
    """
    Ask Apple Mail to synchronize one account or all accounts.

    Args:
        account: Optional account name. When omitted, all accounts are synchronized.

    Returns:
        Summary of synchronization requests. Mail may continue syncing after this tool returns.
    """
    per_account_timeout_s = 8
    safe_account = escape_applescript(account) if account else ""

    if account:
        script = f'''
        tell application "Mail"
            set outputText to "SYNCHRONIZING ACCOUNT" & return & return
            try
                set targetAccount to account "{safe_account}"
                try
                    with timeout of {per_account_timeout_s} seconds
                        synchronize with targetAccount
                    end timeout
                    set outputText to outputText & "Queued sync: " & name of targetAccount & return
                on error errMsg number errNum
                    if errNum is -1712 then
                        set outputText to outputText & "Queued sync: " & name of targetAccount & " (Mail continued after timeout)" & return
                    else
                        return "Error: " & errMsg
                    end if
                end try
            on error errMsg
                return "Error: " & errMsg
            end try
            return outputText
        end tell
        '''
    else:
        script = f"""
        tell application "Mail"
            set outputText to "SYNCHRONIZING ALL ACCOUNTS" & return & return
            try
                set queuedCount to 0
                repeat with targetAccount in every account
                    try
                        with timeout of {per_account_timeout_s} seconds
                            synchronize with targetAccount
                        end timeout
                        set outputText to outputText & "Queued sync: " & name of targetAccount & return
                    on error errMsg number errNum
                        if errNum is -1712 then
                            set outputText to outputText & "Queued sync: " & name of targetAccount & " (Mail continued after timeout)" & return
                        else
                            set outputText to outputText & "Skipped: " & name of targetAccount & " - " & errMsg & return
                        end if
                    end try
                    set queuedCount to queuedCount + 1
                end repeat
                set outputText to outputText & return & "TOTAL ACCOUNTS REQUESTED: " & queuedCount & return
            on error errMsg
                return "Error: " & errMsg
            end try
            return outputText
        end tell
        """

    return run_applescript(script, timeout=60)
