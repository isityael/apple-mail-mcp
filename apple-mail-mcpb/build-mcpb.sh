#!/bin/bash

# Build script for creating Apple Mail MCP Bundle (.mcpb)
# This creates a distributable package for Claude Desktop installation

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SOURCE_DIR="${SCRIPT_DIR}/.."
BUILD_DIR="${SCRIPT_DIR}/build"
OUTPUT_DIR="${SCRIPT_DIR}/../"
PACKAGE_NAME="apple-mail-mcp"
MANIFEST_VERSION=$(grep '"version"' "${SCRIPT_DIR}/manifest.json" | sed -E 's/.*"version": "([^"]+)".*/\1/')
VERSION="${APPLE_MAIL_MCP_VERSION:-${MANIFEST_VERSION}}"

echo -e "${GREEN}Building Apple Mail MCP Bundle v${VERSION}${NC}"
echo "========================================="

# Step 1: Clean build directory
echo -e "\n${YELLOW}Step 1: Cleaning build directory...${NC}"
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

# Step 2: Copy manifest.json
echo -e "\n${YELLOW}Step 2: Copying manifest.json...${NC}"
cp "${SCRIPT_DIR}/manifest.json" "${BUILD_DIR}/"
if [ "${VERSION}" != "${MANIFEST_VERSION}" ]; then
    jq --arg v "${VERSION}" '.version = $v' "${BUILD_DIR}/manifest.json" > "${BUILD_DIR}/manifest.json.tmp"
    mv "${BUILD_DIR}/manifest.json.tmp" "${BUILD_DIR}/manifest.json"
    echo -e "  ${GREEN}ok${NC} Overrode manifest version to ${VERSION}"
fi

# Step 3: Copy Python source files
echo -e "\n${YELLOW}Step 3: Copying Python source files...${NC}"

# Check if source directory exists
if [ ! -d "${SOURCE_DIR}" ]; then
    echo -e "  ${RED}x${NC} Source directory not found: ${SOURCE_DIR}"
    exit 1
fi

# Copy the main Python script
if [ ! -f "${SOURCE_DIR}/apple_mail_mcp.py" ]; then
    echo -e "  ${RED}x${NC} Python script not found: ${SOURCE_DIR}/apple_mail_mcp.py"
    exit 1
fi
cp "${SOURCE_DIR}/apple_mail_mcp.py" "${BUILD_DIR}/"
chmod +x "${BUILD_DIR}/apple_mail_mcp.py"

# Copy pyproject.toml (uv reads this for dependency management)
if [ ! -f "${SOURCE_DIR}/pyproject.toml" ]; then
    echo -e "  ${RED}x${NC} pyproject.toml not found: ${SOURCE_DIR}/pyproject.toml"
    exit 1
fi
cp "${SOURCE_DIR}/pyproject.toml" "${BUILD_DIR}/"

# Copy uv.lock if present (ensures reproducible installs)
if [ -f "${SOURCE_DIR}/uv.lock" ]; then
    cp "${SOURCE_DIR}/uv.lock" "${BUILD_DIR}/"
    echo -e "  ${GREEN}ok${NC} uv.lock included for reproducible installs"
fi

# Step 4: Copy startup wrapper script
echo -e "\n${YELLOW}Step 4: Copying startup wrapper script...${NC}"
if [ ! -f "${SOURCE_DIR}/start_mcp.sh" ]; then
    echo -e "  ${RED}x${NC} Startup script not found: ${SOURCE_DIR}/start_mcp.sh"
    exit 1
fi
cp "${SOURCE_DIR}/start_mcp.sh" "${BUILD_DIR}/"
chmod +x "${BUILD_DIR}/start_mcp.sh"

# Step 5: Copy MCP Package Directory
echo -e "\n${YELLOW}Step 5: Copying MCP package directory...${NC}"
if [ -d "${SOURCE_DIR}/apple_mail_mcp" ]; then
    cp -r "${SOURCE_DIR}/apple_mail_mcp" "${BUILD_DIR}/"
    # Remove __pycache__ directories
    find "${BUILD_DIR}/apple_mail_mcp" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo -e "  ${GREEN}ok${NC} MCP package directory included"
else
    echo -e "  ${RED}x${NC} MCP package directory not found: ${SOURCE_DIR}/apple_mail_mcp"
    exit 1
fi

# Step 5b: Copy Email Management Plugin (optional)
echo -e "\n${YELLOW}Step 5b: Copying Email Management Plugin...${NC}"
if [ -d "${SOURCE_DIR}/skill-email-management" ]; then
    cp -r "${SOURCE_DIR}/skill-email-management" "${BUILD_DIR}/"
    echo -e "  ${GREEN}ok${NC} Email Management Expert Plugin included"
else
    echo -e "  ${YELLOW}--${NC} Plugin directory not found (optional, skipping)"
fi

# Step 5c: Copy UI Module (optional)
echo -e "\n${YELLOW}Step 5c: Copying UI Module...${NC}"
if [ -d "${SOURCE_DIR}/ui" ]; then
    cp -r "${SOURCE_DIR}/ui" "${BUILD_DIR}/"
    # Remove __pycache__ if exists
    find "${BUILD_DIR}/ui" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    echo -e "  ${GREEN}ok${NC} UI Module included"
else
    echo -e "  ${YELLOW}--${NC} UI directory not found (optional, skipping)"
fi

# Note: Virtual environment will be created on user's machine during first run
echo -e "\n${YELLOW}Step 6: Skipping venv creation (will be created on user's machine)...${NC}"
echo -e "  ${GREEN}ok${NC} Venv will be initialized automatically on first run via uv sync"

# Step 7: Create README
echo -e "\n${YELLOW}Step 7: Creating README...${NC}"
cat > "${BUILD_DIR}/README.md" << 'EOF'
# Apple Mail MCP Server

Natural language interface for Apple Mail with 37 email management tools.

## Quick Installation

### Install MCP in Claude Desktop
1. Install this .mcpb file in Claude Desktop (Developer > MCP Servers > Install from file)
2. Grant permissions when prompted for Mail.app access
3. Restart Claude Desktop

### Install Email Management Plugin (Optional)
The plugin teaches Claude intelligent email workflows:

```bash
claude plugin add skill-email-management
```

Or manually copy `skill-email-management/` from this bundle to `~/.claude/skills/email-management`

## Tools (37)

### Inbox & Discovery (7)
- **get_inbox_overview** - Comprehensive inbox status across all accounts
- **list_inbox_emails** - List inbox emails with account/read filters
- **get_unread_count** - Unread count per account
- **list_accounts** - List configured Mail accounts
- **get_recent_emails** - Recent emails with optional content preview
- **list_mailboxes** - Folder hierarchy with message counts
- **inbox_dashboard** - Structured dashboard with per-account metrics

### Search (8)
- **search_emails** - Advanced multi-criteria search (subject, sender, date, attachments, status)
- **get_email_with_content** - Subject search with content preview
- **search_by_sender** - All emails from a specific sender
- **get_recent_from_sender** - Recent emails from sender with time filters
- **search_email_content** - Full-text body search
- **search_all_accounts** - Cross-account subject search
- **get_newsletters** - Identify newsletter subscriptions
- **get_email_thread** - Conversation thread view

### Compose & Reply (4)
- **compose_email** - Send new email with TO/CC/BCC
- **reply_to_email** - Reply to matching email
- **forward_email** - Forward with optional message
- **manage_drafts** - List/create/send/delete drafts

### Manage & Organize (5)
- **move_email** - Move emails by subject keyword (safety limit: 1)
- **bulk_move_emails** - Batch move with higher limits
- **update_email_status** - Mark read/unread, flag/unflag
- **save_email_attachment** - Download attachment to disk
- **manage_trash** - Soft delete, permanent delete, empty trash

### IMAP Sorting (2)
- **sort_inbox** - Rule-based inbox sorting via IMAP (Proton Bridge)
- **imap_bulk_move** - Bulk move between IMAP folders

### Analytics & Export (3)
- **get_statistics** - Account overview, sender stats, mailbox breakdown
- **export_emails** - Export single email or entire mailbox (txt/html)
- **list_email_attachments** - List attachments on matching emails

## Configuration

**Email Preferences (Optional):**
Configure preferences in Claude Desktop settings under this MCP to customize behavior (default account, max results, preferred folders).

**Read-Only Mode (Optional):**
Enable Read-Only Mode in Claude Desktop MCP settings to hide send-capable tools and block draft sending while keeping inbox/search/organization workflows available.

**HTML Compose Support:**
`compose_email` supports an optional HTML body parameter for rich formatting while preserving the plain-text path by default.

## Requirements

- macOS with Apple Mail configured
- Python 3.13+
- uv package manager (https://docs.astral.sh/uv/)
- Mail app with at least one account configured

## Permissions

On first run, macOS will prompt for:
- **Mail.app Control**: Required to automate Mail
- **Mail Data Access**: Required to read email content

## Support

- GitHub: https://github.com/yaelmoshi/apple-mail-mcp
EOF

# Step 8: Create the MCPB package
echo -e "\n${YELLOW}Step 8: Creating MCPB package...${NC}"
cd "${BUILD_DIR}"
OUTPUT_FILE="${OUTPUT_DIR}/${PACKAGE_NAME}-v${VERSION}.mcpb"

# Remove old package if it exists
rm -f "${OUTPUT_FILE}"

# Create zip archive with .mcpb extension
zip -r -q "${OUTPUT_FILE}" . -x "*.DS_Store" "*__MACOSX*" "*.git*"

# Step 9: Verify package
echo -e "\n${YELLOW}Step 9: Verifying package...${NC}"
if [ -f "${OUTPUT_FILE}" ]; then
    FILE_SIZE=$(du -h "${OUTPUT_FILE}" | cut -f1)
    echo -e "  ${GREEN}ok${NC} Package created successfully"
    echo -e "  ${GREEN}ok${NC} Size: ${FILE_SIZE}"
    echo -e "  ${GREEN}ok${NC} Location: ${OUTPUT_FILE}"

    # List contents summary
    echo -e "\n  Package contents:"
    unzip -l "${OUTPUT_FILE}" | tail -1
    echo ""
    unzip -l "${OUTPUT_FILE}" | grep -E '^\s+[0-9]' | head -20
else
    echo -e "  ${RED}x${NC} Failed to create package"
    exit 1
fi

# Step 10: Clean up
echo -e "\n${YELLOW}Step 10: Cleaning up...${NC}"
rm -rf "${BUILD_DIR}"

echo -e "\n${GREEN}=========================================${NC}"
echo -e "${GREEN}Build completed successfully!${NC}"
echo -e "\nPackage: ${GREEN}${OUTPUT_FILE}${NC}"
echo -e "\n${YELLOW}To install:${NC}"
echo -e "  1. Open Claude Desktop > Developer > MCP Servers"
echo -e "  2. Click 'Install from file' and select the .mcpb file"
echo -e "  3. Grant Mail.app permissions when prompted"
echo -e "  4. Restart Claude Desktop"
