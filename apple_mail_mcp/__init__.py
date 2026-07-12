"""Apple Mail MCP - Modular package."""

__version__ = "2.6.1"

from apple_mail_mcp.server import mcp as mcp

# UI availability flag
UI_UNAVAILABLE_REASON = ""
try:
    from ui import create_inbox_dashboard_ui  # noqa: F401

    UI_AVAILABLE = True
except ImportError as exc:
    UI_AVAILABLE = False
    UI_UNAVAILABLE_REASON = str(exc)

# Import all tool modules to register @mcp.tool() decorators
from apple_mail_mcp.tools import (  # noqa: E402
    analytics,  # noqa: F401  (4 tools)
    bulk,  # noqa: F401  (3 tools)
    compose,  # noqa: F401  (4 tools)
    imap_sort,  # noqa: F401  (2 tools)
    inbox,  # noqa: F401  (6 tools)
    manage,  # noqa: F401  (7 tools)
    search,  # noqa: F401  (9 tools)
    smart_inbox,  # noqa: F401  (3 tools)
)
