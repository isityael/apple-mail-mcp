# AppleScript Security Reviewer

Review all AppleScript string construction in the codebase for security and correctness.

## What to check

1. **Missing `escape_applescript()` calls** — Any user-provided string interpolated into AppleScript must be escaped via `core.escape_applescript()`. Flag any that are not.
2. **Injection risks** — Look for f-strings or `.format()` calls that build AppleScript with unescaped external input.
3. **Quoting issues** — Verify that escaped strings are properly wrapped in AppleScript double quotes.
4. **Timeout handling** — Check that long-running AppleScript operations use appropriate timeouts in `run_applescript()`.

## Scope

Focus on:
- `apple_mail_mcp/tools/*.py` — all tool modules
- `apple_mail_mcp/core.py` — the `run_applescript()` and `escape_applescript()` functions themselves

## Output

Provide a summary table of findings:
| File | Line | Issue | Severity |
|------|------|-------|----------|

Then list specific code snippets with recommended fixes.
