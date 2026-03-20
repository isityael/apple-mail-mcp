"""FastMCP server instance and user preferences."""

import os

from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("Apple Mail MCP")

# Load user preferences from environment
USER_PREFERENCES = os.environ.get("USER_EMAIL_PREFERENCES", "")

# Runtime flag set by the shared bootstrap before tool modules are imported.
READ_ONLY = False
