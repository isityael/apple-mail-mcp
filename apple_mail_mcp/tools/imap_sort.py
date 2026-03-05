"""IMAP-based inbox sorting tools (fast direct IMAP, ideal for Proton Bridge)."""

import json
import logging
import os
import time
from collections import defaultdict

from apple_mail_mcp import imap as imap_backend
from apple_mail_mcp.core import inject_preferences
from apple_mail_mcp.server import mcp

# Default rules config path
DEFAULT_RULES_PATH = os.path.expanduser("~/.config/apple-mail-mcp/sort_rules.json")

# Log file for real-time progress (tail -f this)
LOG_PATH = "/tmp/apple-mail-mcp-sort.log"

# Set up file logger
_logger = logging.getLogger("apple-mail-mcp.sort")
_logger.setLevel(logging.INFO)
_handler = logging.FileHandler(LOG_PATH)
_handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
_logger.addHandler(_handler)


def _load_rules(rules_path: str | None = None) -> list[tuple[str, str]]:
    """Load sorting rules from a JSON config file.

    Expected format:
    {
      "rules": [
        {"match": "@amazon.de", "folder": "Shopping/Amazon"},
        {"match": "@github.com", "folder": "IT/Github"},
        {"match": "@facebook.com", "folder": "Trash"}
      ]
    }
    """
    path = rules_path or DEFAULT_RULES_PATH
    if not os.path.exists(path):
        return []

    with open(path) as f:
        data = json.load(f)

    return [(r["match"], r["folder"]) for r in data.get("rules", [])]


def _match_rule(from_header: str, rules: list[tuple[str, str]]) -> str | None:
    """Return the destination folder for a From header, or None."""
    for pattern, destination in rules:
        if pattern.lower() in from_header:
            return destination
    return None


@mcp.tool()
@inject_preferences
def sort_inbox(
    dry_run: bool = True,
    max_emails: int = 0,
    batch_size: int = 0,
    rules_path: str | None = None,
    create_folders: bool = True,
) -> str:
    """
    Sort inbox emails into folders by sender using IMAP (fast, for Proton Bridge).

    Reads sorting rules from ~/.config/apple-mail-mcp/sort_rules.json.
    Each rule maps a sender pattern to a destination folder.

    Writes real-time progress to /tmp/apple-mail-mcp-sort.log (tail -f to monitor).

    Supports batch processing: set batch_size to move N emails per call.
    Call repeatedly until inbox is clean — already-moved emails are skipped.

    Requires config: ~/.config/apple-mail-mcp/imap.json

    Args:
        dry_run: If True, only show what would happen without moving (default: True)
        max_emails: Maximum emails to scan (0 = all)
        batch_size: Max emails to move per call (0 = all matched). Use 100-200 to avoid timeouts.
        rules_path: Optional custom path to sort_rules.json
        create_folders: Auto-create destination folders that don't exist (default: True)

    Returns:
        Summary of emails scanned, matched, and moved
    """
    config = imap_backend.get_imap_config()
    if not config["user"] or not config["password"]:
        return (
            "Error: Set IMAP credentials in ~/.config/apple-mail-mcp/imap.json\n"
            '{"host":"127.0.0.1","port":1143,"user":"you@proton.me","password":"..."}'
        )

    rules = _load_rules(rules_path)
    if not rules:
        path = rules_path or DEFAULT_RULES_PATH
        return (
            f"No sorting rules found at {path}\n\n"
            "Create it with format:\n"
            '{"rules": [{"match": "@amazon.de", "folder": "Shopping/Amazon"}, ...]}'
        )

    lines = []
    mode = "DRY RUN" if dry_run else ("BATCH " + str(batch_size) if batch_size else "FULL")
    lines.append(f"INBOX SORT ({mode})")
    lines.append("")

    _logger.info("=" * 60)
    _logger.info("INBOX SORT started (%s)", mode)

    try:
        conn = imap_backend.connect(config["host"], config["port"], config["user"], config["password"])
    except Exception as e:
        _logger.error("Connection failed: %s", e)
        return f"Error connecting to IMAP: {e}"

    try:
        # Get existing folders and build resolution map
        existing = imap_backend.list_folders(conn)
        _logger.info("Loaded %d existing folders", len(existing))

        # Resolve all destination folder names to IMAP paths
        dest_map: dict[str, str] = {}  # human name -> IMAP path
        for _, dest in rules:
            if dest != "Trash" and dest not in dest_map:
                dest_map[dest] = imap_backend.resolve_folder(dest, existing)

        # Create missing folders
        if create_folders:
            missing = {human: imap_path for human, imap_path in dest_map.items() if imap_path not in existing}
            if missing:
                lines.append("── Folder creation ──")
                for human in sorted(missing):
                    imap_path = missing[human]
                    if dry_run:
                        lines.append(f"  + would create: {human} ({imap_path})")
                    else:
                        ok = imap_backend.create_folder(conn, imap_path)
                        status = "✓ created" if ok else "✗ failed"
                        lines.append(f"  {status}: {human}")
                        _logger.info("Folder %s: %s -> %s", status, human, imap_path)
                        if ok:
                            existing.add(imap_path)
                lines.append("")

        # Scan inbox
        _logger.info("Scanning INBOX...")
        t0 = time.time()
        conn.select("INBOX", readonly=dry_run)
        headers = imap_backend.batch_fetch_from_headers(conn)
        total = len(headers)
        scan_time = time.time() - t0
        _logger.info("Scanned %d messages in %.1fs", total, scan_time)

        if max_emails and max_emails < total:
            headers = headers[:max_emails]

        lines.append(f"── Scanned {total} messages in INBOX ({scan_time:.1f}s) ──")
        if max_emails:
            lines.append(f"   (processing first {max_emails})")
        lines.append("")

        # Match rules
        plan: dict[str, list[bytes]] = defaultdict(list)
        unmatched = 0

        for uid, from_header in headers:
            dest = _match_rule(from_header, rules)
            if dest:
                plan[dest].append(uid)
            else:
                unmatched += 1

        # Flatten plan into a move list for batch limiting
        all_moves: list[tuple[str, bytes]] = []
        for dest in sorted(plan.keys()):
            for uid in plan[dest]:
                all_moves.append((dest, uid))

        total_matched = len(all_moves)
        if batch_size and batch_size < total_matched:
            all_moves = all_moves[:batch_size]

        # Summary by destination
        batch_plan: dict[str, int] = defaultdict(int)
        for dest, _ in all_moves:
            batch_plan[dest] += 1

        lines.append("── Plan ──")
        trash_count = 0
        move_count = 0
        for dest in sorted(plan.keys()):
            full_count = len(plan[dest])
            batch_count = batch_plan.get(dest, 0)
            if dest == "Trash":
                trash_count += batch_count
                if batch_size and batch_count < full_count:
                    lines.append(f"  {batch_count:>5}/{full_count}  🗑 trash")
                else:
                    lines.append(f"  {full_count:>5}  🗑 trash")
            else:
                move_count += batch_count
                if batch_size and batch_count < full_count:
                    lines.append(f"  {batch_count:>5}/{full_count}  → {dest}")
                else:
                    lines.append(f"  {full_count:>5}  → {dest}")

        lines.append("")
        lines.append(
            f"  {move_count:>5}  to move"
            + (
                f" (of {total_matched - sum(1 for d, _ in [(d, u) for d, u in all_moves if d == 'Trash'])})"
                if batch_size and len(all_moves) < total_matched
                else ""
            )
        )
        lines.append(f"  {trash_count:>5}  to trash")
        lines.append(f"  {unmatched:>5}  no match (stay in INBOX)")
        if batch_size and len(all_moves) < total_matched:
            lines.append(f"  batch: {len(all_moves)}/{total_matched} (call again for next batch)")

        if dry_run:
            lines.append("")
            lines.append("Dry run complete. Set dry_run=False to execute.")
            _logger.info("Dry run: %d to move, %d to trash, %d unmatched", move_count, trash_count, unmatched)
            return "\n".join(lines)

        # Execute moves
        lines.append("")
        lines.append("── Moving ──")
        _logger.info("Starting moves: %d emails", len(all_moves))

        # Re-select as read-write
        conn.select("INBOX", readonly=False)

        succeeded = 0
        failed = 0
        t0 = time.time()

        # Group by destination for cleaner logging
        current_dest = None
        dest_succeeded = 0
        dest_failed = 0

        for i, (dest, uid) in enumerate(all_moves, 1):
            if dest != current_dest:
                # Log previous destination results
                if current_dest is not None:
                    imap_label = "Trash" if current_dest == "Trash" else current_dest
                    _logger.info("  %s: ✓ %d  ✗ %d", imap_label, dest_succeeded, dest_failed)
                    label = "🗑 trash" if current_dest == "Trash" else f"→ {current_dest}"
                    status = f"✓ {dest_succeeded}"
                    if dest_failed:
                        status += f"  ✗ {dest_failed} failed"
                    lines.append(f"  {label:40} {status}")
                current_dest = dest
                dest_succeeded = 0
                dest_failed = 0
                _logger.info("Moving to %s ...", dest)

            imap_dest = dest_map.get(dest, dest)  # Trash stays as "Trash"
            if imap_backend.move_message(conn, uid, imap_dest):
                succeeded += 1
                dest_succeeded += 1
            else:
                failed += 1
                dest_failed += 1

            # Log progress every 25 moves
            if i % 25 == 0:
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                _logger.info("  progress: %d/%d (%.1f/s)", i, len(all_moves), rate)

        # Log last destination
        if current_dest is not None:
            imap_label = "Trash" if current_dest == "Trash" else current_dest
            _logger.info("  %s: ✓ %d  ✗ %d", imap_label, dest_succeeded, dest_failed)
            label = "🗑 trash" if current_dest == "Trash" else f"→ {current_dest}"
            status = f"✓ {dest_succeeded}"
            if dest_failed:
                status += f"  ✗ {dest_failed} failed"
            lines.append(f"  {label:40} {status}")

        conn.expunge()
        elapsed = time.time() - t0

        lines.append("")
        lines.append(
            f"✓ Done in {elapsed:.1f}s — moved {succeeded}, trashed {trash_count}"
            + (f", {failed} failed" if failed else "")
        )
        if batch_size and len(all_moves) < total_matched:
            remaining = total_matched - len(all_moves)
            lines.append(f"  {remaining} emails remaining — call sort_inbox again for next batch")

        _logger.info("Done: %d moved, %d failed in %.1fs", succeeded, failed, elapsed)

    finally:
        try:
            conn.logout()
        except Exception:
            pass

    return "\n".join(lines)


@mcp.tool()
@inject_preferences
def imap_bulk_move(
    from_mailbox: str,
    to_mailbox: str,
    sender: str | None = None,
    max_moves: int = 100,
    dry_run: bool = True,
) -> str:
    """
    Move emails between IMAP folders directly (fast, for Proton Bridge).

    Much faster than AppleScript for large mailboxes. Optionally filter by sender.
    Writes progress to /tmp/apple-mail-mcp-sort.log (tail -f to monitor).

    Requires config: ~/.config/apple-mail-mcp/imap.json

    Args:
        from_mailbox: Source mailbox (e.g., "INBOX", "Rechnungen", "IT/Netflix")
        to_mailbox: Destination mailbox (e.g., "Finanzen/Rechnungen")
        sender: Optional sender pattern to filter by (case-insensitive substring)
        max_moves: Maximum emails to move (safety limit, default: 100)
        dry_run: If True, only count matches without moving (default: True)

    Returns:
        Summary of moved emails
    """
    config = imap_backend.get_imap_config()
    if not config["user"] or not config["password"]:
        return (
            "Error: Set IMAP credentials in ~/.config/apple-mail-mcp/imap.json\n"
            '{"host":"127.0.0.1","port":1143,"user":"you@proton.me","password":"..."}'
        )

    try:
        conn = imap_backend.connect(config["host"], config["port"], config["user"], config["password"])
    except Exception as e:
        return f"Error connecting to IMAP: {e}"

    lines = []
    lines.append(f"IMAP BULK MOVE{' (DRY RUN)' if dry_run else ''}")
    lines.append(f"{from_mailbox} → {to_mailbox}")

    _logger.info("BULK MOVE: %s → %s%s", from_mailbox, to_mailbox, " (dry run)" if dry_run else "")

    try:
        # Resolve folder names to IMAP paths
        existing = imap_backend.list_folders(conn)
        imap_from = imap_backend.resolve_folder(from_mailbox, existing)
        imap_to = imap_backend.resolve_folder(to_mailbox, existing)

        # Ensure destination exists
        if imap_to not in existing and to_mailbox != "Trash":
            if dry_run:
                lines.append(f"Note: folder '{to_mailbox}' does not exist (would create as {imap_to})")
            else:
                imap_backend.create_folder(conn, imap_to)
                lines.append(f"Created folder: {to_mailbox}")
                _logger.info("Created folder: %s (%s)", to_mailbox, imap_to)

        conn.select(f'"{imap_from}"', readonly=dry_run)
        headers = imap_backend.batch_fetch_from_headers(conn)
        lines.append(f"Found {len(headers)} message(s) in {from_mailbox}")

        # Filter by sender if specified
        if sender:
            sender_lower = sender.lower()
            matched = [(uid, fh) for uid, fh in headers if sender_lower in fh]
            lines.append(f"Matched {len(matched)} by sender '{sender}'")
        else:
            matched = headers

        to_move = matched[:max_moves] if max_moves else matched

        if dry_run:
            lines.append(f"\nWould move {len(to_move)} email(s).")
            lines.append("Set dry_run=False to execute.")
            _logger.info("Dry run: would move %d", len(to_move))
            return "\n".join(lines)

        # Re-select as read-write
        conn.select(f'"{imap_from}"', readonly=False)

        moved = 0
        failed = 0
        t0 = time.time()
        _logger.info("Moving %d emails from %s to %s ...", len(to_move), from_mailbox, to_mailbox)

        for i, (uid, _) in enumerate(to_move, 1):
            if imap_backend.move_message(conn, uid, imap_to):
                moved += 1
            else:
                failed += 1

            if i % 25 == 0:
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                _logger.info("  progress: %d/%d (%.1f/s)", i, len(to_move), rate)

        conn.expunge()
        elapsed = time.time() - t0

        lines.append(f"\n✓ Moved {moved} email(s) in {elapsed:.1f}s")
        if failed:
            lines.append(f"✗ {failed} failed")

        _logger.info("Done: %d moved, %d failed in %.1fs", moved, failed, elapsed)

    finally:
        try:
            conn.logout()
        except Exception:
            pass

    return "\n".join(lines)
