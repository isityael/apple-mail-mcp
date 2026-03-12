---
name: applescript-patterns
description: AppleScript code generation patterns for Apple Mail MCP tools. Use when creating, modifying, or reviewing AppleScript templates in tool functions under apple_mail_mcp/tools/. Ensures correct escaping, mailbox fallback, recipient handling, iteration direction, and helper usage.
user-invocable: false
---

# AppleScript Generation Patterns

Follow these patterns when writing or modifying AppleScript templates in `apple_mail_mcp/tools/*.py`.

## Escaping User Input

NEVER interpolate user strings directly into AppleScript f-strings. Always use `escape_applescript()` from `core.py`:

```python
from apple_mail_mcp.core import escape_applescript

safe_account = escape_applescript(account)
safe_keyword = escape_applescript(subject_keyword)

script = f'''
tell application "Mail"
    set targetAccount to account "{safe_account}"
    ...
end tell
'''
```

`escape_applescript()` handles: backslashes, double quotes, carriage returns, newlines, and tabs. This prevents AppleScript injection via user-controlled strings.

## Mailbox Handling

Use `get_mailbox_script()` for any mailbox access. It handles the INBOX/Inbox inconsistency in macOS Mail:

```python
from apple_mail_mcp.core import get_mailbox_script

# Generates try/catch with INBOX->Inbox fallback
mailbox_setup = get_mailbox_script(mailbox, "targetMailbox", "targetAccount")

script = f'''
tell application "Mail"
    set targetAccount to account "{safe_account}"
    {mailbox_setup}
    ...
end tell
'''
```

For inbox specifically, use `inbox_mailbox_script()`:

```python
from apple_mail_mcp.core import inbox_mailbox_script

script = f'''
    {inbox_mailbox_script("inboxMailbox", "targetAccount")}
'''
```

## Recipients (TO/CC/BCC)

Use `recipients_script()` for adding recipients to outgoing messages:

```python
from apple_mail_mcp.core import recipients_script

to_script = recipients_script(to, "to", "newMessage")
cc_script = recipients_script(cc, "cc", "newMessage")
bcc_script = recipients_script(bcc, "bcc", "newMessage")
```

This handles comma-separated addresses, escaping each one, and generating the correct `make new ... recipient` AppleScript.

## Iteration Direction

When mutating a collection (moving or deleting messages), ALWAYS iterate in reverse to avoid index shifting:

```applescript
-- CORRECT: reverse iteration during mutation
set msgCount to count of inboxMessages
repeat with i from msgCount to 1 by -1
    set aMessage to item i of inboxMessages
    move aMessage to targetMailbox
end repeat

-- WRONG: forward iteration skips items after mutation
repeat with aMessage in inboxMessages
    move aMessage to targetMailbox  -- shifts subsequent indices
end repeat
```

Forward iteration is fine for read-only operations (searching, listing).

## Script Execution

All AppleScript goes through `run_applescript()` which pipes via stdin to `osascript -`:

```python
from apple_mail_mcp.core import run_applescript

result = run_applescript(script)
```

- 120-second timeout
- Raises `Exception` on non-zero return code or timeout
- Returns stdout stripped

## Other Shared Helpers

Available in `core.py`:

| Helper | Purpose |
|--------|---------|
| `date_cutoff_script(days_back, var_name)` | AppleScript snippet for date cutoff |
| `content_preview_script(max_length, output_var)` | Truncated content preview extraction |
| `skip_folders_condition(var_name)` | Condition to skip Trash/Junk/Sent/Drafts |
| `LOWERCASE_HANDLER` | AppleScript `on lowercase(str)` handler |

## Tool Registration

Add `@mcp.tool()` and `@inject_preferences` decorators:

```python
from apple_mail_mcp.core import inject_preferences
from apple_mail_mcp.server import mcp

@mcp.tool()
@inject_preferences
def my_new_tool(account: str, ...) -> str:
    """Tool docstring (becomes the MCP tool description)."""
    ...
```

The tool auto-registers when its module is imported by `__init__.py`.

## Output Pattern

Build output as a string variable in AppleScript and return it:

```applescript
set outputText to "TOOL RESULT" & return & return
-- ... build up outputText ...
return outputText
```

## Error Handling

Wrap the main logic in try/on error:

```applescript
try
    -- main logic
on error errMsg
    return "Error: " & errMsg
end try
```
