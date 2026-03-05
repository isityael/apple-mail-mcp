# Email Management Plugin

A Claude Code plugin that provides expert email management workflows for the [Apple Mail MCP](https://github.com/isityael/apple-mail-mcp) server.

## What It Does

- **Apple Mail MCP** = The tools (29 email management functions)
- **This Plugin** = The expertise (workflows, strategies, best practices)

Together, they create an intelligent email management assistant that knows both what it CAN do and HOW to do it effectively.

## Installation

### As Claude Code Plugin (Recommended)

```bash
claude plugin add /path/to/skill-email-management
# or
claude --plugin-dir /path/to/skill-email-management
```

### Manual Installation

**User scope** (all projects):
```bash
cp -r skill-email-management/skills/email-management ~/.claude/skills/email-management
```

**Project scope** (current project only):
```bash
mkdir -p .claude/skills
cp -r skill-email-management/skills/email-management .claude/skills/email-management
```

## Usage

The skill activates automatically when you mention email management topics:

- "Help me triage my inbox"
- "Organize my emails by project"
- "Find all emails from John about Alpha"
- "Clean up old newsletters"
- "Sort my Proton Bridge inbox"
- "Achieve inbox zero"

## What's Included

```
skill-email-management/
  .claude-plugin/
    plugin.json              # Plugin manifest
  skills/
    email-management/
      SKILL.md               # Core skill (29-tool reference, workflows, guidelines)
      references/
        search-patterns.md   # Search pattern quick-reference
        workflows.md         # Ready-to-use workflow templates
  README.md
```

### Key Features

- **Full 29-tool coverage** including IMAP sorting, bulk moves, newsletters, and dashboard
- **6 built-in workflows**: daily triage, inbox zero, IMAP sorting, bulk cleanup, search, vacation recovery
- **Tool selection guide**: which tool to use for each goal
- **Safety guidelines**: default limits, confirmation patterns, export-before-delete
- **Lean references**: consolidated search patterns and workflow templates

## Requirements

- Claude Code (with plugin/skill support)
- Apple Mail MCP server (this repository)
- macOS with Apple Mail

## Version

- **Plugin version**: 2.0.0
- **Compatible with**: Apple Mail MCP v2.0+ (29 tools)

## License

MIT License - Same as Apple Mail MCP
