"""Runtime bootstrap shared by script and module entrypoints."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from typing import Any

import apple_mail_mcp.server as server

SEND_TOOL_NAMES = ("compose_email", "reply_to_email", "forward_email")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the MCP server."""
    parser = argparse.ArgumentParser(description="Apple Mail MCP Server")
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Disable tools that send email. Draft listing/creation remains available.",
    )
    return parser.parse_args(argv)


def _fallback_remove_tool(mcp: Any, name: str) -> None:
    """Remove a tool via internal manager fallback when public API is unavailable."""
    tool_manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None)
    if isinstance(tools, dict):
        tools.pop(name, None)


def apply_read_only_mode(mcp: Any, read_only: bool) -> None:
    """Remove send-capable tools when read-only mode is enabled."""
    if not read_only:
        return

    for name in SEND_TOOL_NAMES:
        try:
            if hasattr(mcp, "remove_tool"):
                mcp.remove_tool(name)
            else:
                _fallback_remove_tool(mcp, name)
        except (KeyError, ValueError):
            continue


def configure_runtime(
    argv: Sequence[str] | None = None,
    package_loader: Callable[[], Any] | None = None,
) -> tuple[argparse.Namespace, Any]:
    """Configure runtime flags and return the initialized MCP server."""
    args = parse_args(argv)
    server.READ_ONLY = args.read_only

    if package_loader is None:
        from apple_mail_mcp import mcp as loaded_mcp
    else:
        loaded_mcp = package_loader()

    apply_read_only_mode(loaded_mcp, args.read_only)
    return args, loaded_mcp


def main(argv: Sequence[str] | None = None) -> None:
    """Run the MCP server after applying runtime configuration."""
    _, mcp = configure_runtime(argv)
    mcp.run()
