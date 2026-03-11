"""Entry point for `python -m apple_mail_mcp`."""

from apple_mail_mcp.server import mcp

# Import tool modules to register @mcp.tool() decorators
from apple_mail_mcp.tools import (
    analytics,  # noqa: F401
    bulk,  # noqa: F401
    compose,  # noqa: F401
    imap_sort,  # noqa: F401
    inbox,  # noqa: F401
    manage,  # noqa: F401
    search,  # noqa: F401
    smart_inbox,  # noqa: F401
)

mcp.run()
