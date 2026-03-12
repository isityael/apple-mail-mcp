---
name: test-tool
description: Test a specific apple-mail-mcp tool by running it against the local MCP server
disable-model-invocation: true
---

# Test Tool

Manually test a specific MCP tool from this project.

## Arguments
- `tool_name` (required): The name of the tool to test (e.g., `get_unread_count`, `list_accounts`)
- `args` (optional): JSON arguments to pass to the tool

## Steps

1. Ensure the MCP server is running (check with `ps aux | grep apple_mail_mcp`)
2. Use the corresponding `mcp__apple-mail__<tool_name>` tool to invoke it
3. Report the result, including any errors or unexpected output
4. If the tool modifies state (move, compose, update), confirm with the user before executing
