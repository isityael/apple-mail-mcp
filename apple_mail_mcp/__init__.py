"""Apple Mail MCP - Modular package."""

from apple_mail_mcp.server import mcp as mcp

# UI availability flag
try:
    from ui import create_inbox_dashboard_ui  # noqa: F401

    UI_AVAILABLE = True
except ImportError:
    UI_AVAILABLE = False

# Import all tool modules to register @mcp.tool() decorators
from apple_mail_mcp.tools import (
    analytics,  # noqa: F401  (4 tools)
    bulk,  # noqa: F401  (3 tools)
    compose,  # noqa: F401  (4 tools)
    imap_sort,  # noqa: F401  (2 tools)
    inbox,  # noqa: F401  (6 tools)
    manage,  # noqa: F401  (6 tools)
    search,  # noqa: F401  (9 tools)
    smart_inbox,  # noqa: F401  (3 tools)
)
