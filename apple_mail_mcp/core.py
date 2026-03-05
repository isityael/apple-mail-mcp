"""Core helpers: AppleScript execution, escaping, parsing, and preference injection."""

import subprocess

from apple_mail_mcp.server import USER_PREFERENCES


def inject_preferences(func):
    """Decorator that appends user preferences to tool docstrings"""
    if USER_PREFERENCES:
        if func.__doc__:
            func.__doc__ = func.__doc__.rstrip() + f"\n\nUser Preferences: {USER_PREFERENCES}"
        else:
            func.__doc__ = f"User Preferences: {USER_PREFERENCES}"
    return func


def escape_applescript(value: str) -> str:
    """Escape a string for safe injection into AppleScript double-quoted strings.

    Escapes backslashes first, then double quotes, then control characters that
    could break out of the string context or inject AppleScript commands.
    """
    return (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    )


def run_applescript(script: str) -> str:
    """Execute AppleScript via stdin pipe for reliable multi-line handling"""
    try:
        result = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=120)
        if result.returncode != 0 and result.stderr.strip():
            raise Exception(f"AppleScript error: {result.stderr.strip()}")
        return result.stdout.strip()
    except subprocess.TimeoutExpired as e:
        raise Exception("AppleScript execution timed out") from e
    except Exception as e:
        raise Exception(f"AppleScript execution failed: {e}") from e


# ---------------------------------------------------------------------------
# Shared AppleScript template helpers
# ---------------------------------------------------------------------------

LOWERCASE_HANDLER = """
    on lowercase(str)
        set lowerStr to do shell script "echo " & quoted form of str & " | tr '[:upper:]' '[:lower:]'"
        return lowerStr
    end lowercase
"""


def inbox_mailbox_script(var_name: str = "inboxMailbox", account_var: str = "anAccount") -> str:
    """Return AppleScript snippet to get inbox mailbox with INBOX/Inbox fallback."""
    return f"""
                try
                    set {var_name} to mailbox "INBOX" of {account_var}
                on error
                    set {var_name} to mailbox "Inbox" of {account_var}
                end try"""


def content_preview_script(max_length: int, output_var: str = "outputText") -> str:
    """Return AppleScript snippet to extract and truncate email content preview."""
    return f"""
                            try
                                set msgContent to content of aMessage
                                set AppleScript's text item delimiters to {{return, linefeed}}
                                set contentParts to text items of msgContent
                                set AppleScript's text item delimiters to " "
                                set cleanText to contentParts as string
                                set AppleScript's text item delimiters to ""

                                if length of cleanText > {max_length} then
                                    set contentPreview to text 1 thru {max_length} of cleanText & "..."
                                else
                                    set contentPreview to cleanText
                                end if

                                set {output_var} to {output_var} & "   Content: " & contentPreview & return
                            on error
                                set {output_var} to {output_var} & "   Content: [Not available]" & return
                            end try"""


def get_mailbox_script(mailbox_name: str, var_name: str = "targetMailbox", account_var: str = "targetAccount") -> str:
    """Return AppleScript snippet to get a mailbox with INBOX/Inbox fallback.

    For any mailbox name, tries to get it directly, then falls back to
    "Inbox" if the name is "INBOX" (macOS Mail inconsistency).
    """
    safe = escape_applescript(mailbox_name)
    return f"""
                try
                    set {var_name} to mailbox "{safe}" of {account_var}
                on error
                    if "{safe}" is "INBOX" then
                        set {var_name} to mailbox "Inbox" of {account_var}
                    else
                        error "Mailbox not found: {safe}"
                    end if
                end try"""


def recipients_script(
    addresses_csv: str | None,
    recipient_type: str,
    message_var: str = "newMessage",
) -> str:
    """Return AppleScript snippet to add TO/CC/BCC recipients.

    Args:
        addresses_csv: Comma-separated email addresses, or None
        recipient_type: "to", "cc", or "bcc"
        message_var: AppleScript variable name of the message
    """
    if not addresses_csv:
        return ""
    lines = []
    for addr in addresses_csv.split(","):
        safe_addr = escape_applescript(addr.strip())
        lines.append(
            f"make new {recipient_type} recipient at end of "
            f"{recipient_type} recipients of {message_var} "
            f'with properties {{address:"{safe_addr}"}}'
        )
    return "\n            ".join(lines)


def date_cutoff_script(days_back: int, var_name: str = "cutoffDate") -> str:
    """Return AppleScript snippet to set a date cutoff variable."""
    if days_back <= 0:
        return ""
    return f"""
            set {var_name} to (current date) - ({days_back} * days)"""


def skip_folders_condition(var_name: str = "mailboxName") -> str:
    """Return AppleScript condition to skip system folders (Trash, Junk, etc)."""
    from apple_mail_mcp.constants import SKIP_FOLDERS

    folder_list = ", ".join(f'"{f}"' for f in SKIP_FOLDERS)
    return f"{var_name} is not in {{{folder_list}}}"
