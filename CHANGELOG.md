# Changelog

All notable changes to the Apple Mail MCP Server will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **CI/CD**: Migrated to a single Woodpecker pipeline in `.woodpecker.yml` and removed the GitHub Actions workflow

## [2.1.0] - 2026-03-11

### Added
- **Merged upstream `patrickfreyer/apple-mail-mcp`** through commit `5a0bc15` — all upstream features since the v2.0.0 fork divergence
- **8 new upstream tools**: `search_emails_advanced`, `create_mailbox`, `archive_emails`, `mark_emails`, `delete_emails`, `get_awaiting_reply`, `get_needs_response`, `get_top_senders` (via `bulk.py` and `smart_inbox.py`)
- **Upstream `bulk_move_emails`** with dry-run and filter requirements (replaces fork's simpler AppleScript version)
- **Security hardening** from upstream: `escape_applescript` handles `\r\n` normalisation, `_sanitize_for_json()` for MCP transport safety, `save_email_attachment` path validation, `export_emails` cap, bulk operation safety guards
- **`whose` clause performance** from upstream — faster searches in large mailboxes
- **Attachment support**: `list_email_attachments`, `save_email_attachment` with security path validation
- **Open/draft modes** for compose, reply, and manage_drafts
- **`parse_email_list()`** and `build_*` helpers in core.py from upstream

### Changed
- Tool count updated to 37 (from 29): 35 upstream + 2 IMAP sort tools
- Manifest version bumped to 2.1.0
- `core.py` now includes upstream's `build_mailbox_ref()`, `build_filter_condition()`, `build_date_filter()`, `build_email_fields_script()` alongside fork's `get_mailbox_script()` and `recipients_script()`
- `__main__.py` now registers all 8 tool modules

### Preserved from fork
- **IMAP backend** (`imap.py`): Direct IMAP operations with SSL/STARTTLS/plain fallback for Proton Bridge
- **IMAP sort tools** (`imap_sort.py`): `sort_inbox` and `imap_bulk_move` for fast rule-based sorting
- **CI/CD**: GitHub Actions and Woodpecker pipelines
- **Packaging**: `pyproject.toml` + `uv.lock` (no `requirements.txt`)
- **Skill plugin**: `skill-email-management/` with Claude Code plugin structure
- **Renovate**: Automated dependency updates

## [2.0.0] - 2026-03-05

### Added
- **IMAP-based inbox sorting** (`sort_inbox`): Sort emails into folders using IMAP with Proton Bridge support (SSL/STARTTLS/plain fallback)
- **IMAP bulk move** (`imap_bulk_move`): Batch move emails between IMAP folders with RFC 6851 MOVE support
- **Bulk move emails** (`bulk_move_emails`): AppleScript-based bulk move for folder merging and inbox triage
- **CI/CD pipelines**: GitHub Actions (`.github/workflows/ci.yml`) and Woodpecker CI (`.woodpecker/`) with lint, format check, syntax/import validation, mcpb build, and release-on-tag
- **CLAUDE.md**: Project instructions for Claude Code with architecture docs and development patterns

### Changed
- **Modular package refactoring**: Split monolithic `apple_mail_mcp.py` into `apple_mail_mcp/` package with `server.py`, `core.py`, `constants.py`, `imap.py`, and `tools/` submodules
- **Core hardened**: Deduplicated AppleScript helpers into `core.py` (`get_mailbox_script()`, `recipients_script()`, `inbox_mailbox_script()`, `date_cutoff_script()`, `content_preview_script()`, `skip_folders_condition()`)
- **Migrated to `pyproject.toml` + `uv sync`** from `requirements.txt`
- Tool count updated to 29 (from 26)

### Fixed
- IMAP modified UTF-7 encoding for international mailbox names
- IMAP config file support (`~/.config/apple-mail-mcp/imap.json`)
- Multiple CI lint failures and Woodpecker YAML escaping issues
- Ruff lint errors in `ui/dashboard.py`

## [1.6.1] - 2026-03-10

### Security (upstream)

- **`escape_applescript` now escapes newlines and tabs** -- prevents AppleScript syntax errors when user input contains `\n`, `\r`, or `\t` characters
- **`empty_trash` now enforces `max_deletes` limit** -- added `confirm_empty` boolean parameter; action is rejected unless explicitly confirmed
- **Bulk operations require filters** -- `manage_trash` (move_to_trash, delete_permanent) and `update_email_status` now require at least one filter (`subject_keyword` or `sender`), or explicit `apply_to_all=True`, to prevent accidental bulk modifications
- **`save_email_attachment` path validation** -- save path must resolve under the user's home directory; writes to `~/.ssh`, `~/.aws`, `~/Library/LaunchAgents`, and other sensitive directories are blocked
- **`export_emails` entire mailbox cap** -- added `max_emails` parameter (default: 1000) to prevent unbounded exports when `scope="entire_mailbox"`
- **Pinned dependency versions** -- `requirements.txt` now uses exact versions (`fastmcp==3.1.0`, `mcp-ui-server==1.0.0`) instead of open-ended `>=` ranges

## [1.6.0] - 2026-02-06

### Added
- **CC/BCC support on `reply_to_email`**: Optional `cc` and `bcc` parameters for adding recipients when replying
- **CC/BCC support on `forward_email`**: Optional `cc` and `bcc` parameters for adding recipients when forwarding
- **`get_recent_from_sender`**: Retrieve recent emails from a sender with human-friendly time filters (today, week, month, all)
- **`inbox_dashboard`**: Interactive UI dashboard resource for compatible MCP clients (requires `mcp-ui-server`)

### Changed
- AppleScript execution now uses **stdin pipe** (`osascript -` with `subprocess.run(input=...)`) instead of `-e` flag, fixing reliability issues with multi-line scripts and special characters
- Improved error surfacing: AppleScript stderr is now properly captured and raised
- Tool count updated to 26 (from 25)
- README fully rewritten for conciseness and scannability

### Fixed
- Multi-line AppleScript commands that previously failed due to shell escaping now execute reliably via stdin
- AppleScript timeout handling consolidated (120s default)

## [1.5.0] - 2026-02-01

### Added
- **search_by_sender**: Find emails from a specific sender across mailboxes
- **search_all_accounts**: Cross-account search with advanced filtering
- **search_email_content**: Full-text search in email bodies
- **get_newsletters**: Find newsletter and subscription emails

### Changed
- Updated manifest to include 4 new search tools (total: 24 tools)
- Enhanced search capabilities across the server

## [1.4.0] - 2025-10-14

### Added
- **User Preferences Configuration**: New configurable preference string in MCPB user_config

### Changed
- Updated manifest.json to include user_config section (version 1.4.0)
- Enhanced all 20 tool functions with @inject_preferences decorator

## [1.3.0] - 2025-10-14

### Added
- **search_emails**, **update_email_status**, **manage_trash**, **forward_email**, **get_email_thread**, **manage_drafts**, **get_statistics**, **export_emails** (8 new tools)

### Changed
- Updated manifest to include all 8 new tools (total: 20 tools)

## [1.2.0] - 2025-10-14

### Added
- **get_inbox_overview**: Email preview section

## [1.1.0] - 2025-10-14

### Added
- **get_inbox_overview**: Comprehensive inbox dashboard

## [1.0.0] - 2025-10-14

### Added
- Initial release of Apple Mail MCP Server

---

## Version History Summary

- **v2.1.0** - Merged upstream (8 new tools, security hardening, `whose` clause perf, attachments, open/draft modes, smart inbox). Total: 37 tools
- **v2.0.0** - Modular refactoring, IMAP sorting/bulk-move tools, CI/CD (GitHub Actions + Woodpecker), pyproject.toml migration
- **v1.6.1** - Security hardening: input escaping, path validation, bulk operation safeguards, export caps, dependency pinning
- **v1.6.0** - CC/BCC on reply/forward, stdin-based AppleScript execution, interactive dashboard, README rewrite
- **v1.5.0** - Advanced search tools (4 new tools)
- **v1.4.0** - User preferences configuration
- **v1.3.0** - Major feature expansion (8 new tools)
- **v1.2.0** - Enhanced overview with email preview
- **v1.1.0** - Added inbox overview dashboard
- **v1.0.0** - Initial release with core functionality

## Upgrade Notes

### Upgrading to 2.1.0
- No breaking changes from 2.0.0
- 8 new tools available immediately (bulk ops, smart inbox, advanced search, create_mailbox, archive)
- Bulk operations now have stricter safety guards (dry_run defaults, filter requirements)
- CI tool-count assertions updated from 29 to 37
- Rebuild `.mcpb` bundle to include new tools

### Upgrading to 2.0.0
- **Breaking**: Entry point is now `apple_mail_mcp/` package instead of monolithic `apple_mail_mcp.py`
- **Breaking**: Dependencies managed via `pyproject.toml` + `uv sync` (replaces `requirements.txt`)
- `start_mcp.sh` handles setup automatically
- 3 new IMAP tools require IMAP config for Proton Bridge (`~/.config/apple-mail-mcp/imap.json` or env vars)
- Rebuild `.mcpb` bundle to include new tools
