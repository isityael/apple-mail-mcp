#!/usr/bin/env python3
"""Cross-account IMAP move: search by TO recipient and move between accounts.

Usage:
    uv run python scripts/cross_account_move.py --dry-run
    uv run python scripts/cross_account_move.py
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import time
from collections import defaultdict

sys.path.insert(0, ".")

from apple_mail_mcp import imap as imap_backend
from apple_mail_mcp.constants import SKIP_FOLDERS


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-account IMAP move")
    parser.add_argument("--src-account", default="yael@m0sh1.cc", help="Source account name in imap.json")
    parser.add_argument("--dst-account", default="m0sh1", help="Destination account name in imap.json")
    parser.add_argument("--to", default="yael@m0sh1.cc", help="TO address to search for")
    parser.add_argument("--dry-run", action="store_true", help="Preview without moving")
    parser.add_argument("--max-results", type=int, default=0, help="Max emails to move (0 = all)")
    args = parser.parse_args()

    # Load configs
    src_cfg = imap_backend.get_account_config(args.src_account)
    dst_cfg = imap_backend.get_account_config(args.dst_account)
    if not src_cfg:
        print(f"Error: no IMAP config for source account '{args.src_account}'")
        sys.exit(1)
    if not dst_cfg:
        print(f"Error: no IMAP config for destination account '{args.dst_account}'")
        sys.exit(1)

    # Connect to both
    print(f"Connecting to source: {args.src_account} ({src_cfg['host']}:{src_cfg['port']})")
    src_conn = imap_backend.connect(src_cfg["host"], src_cfg["port"], src_cfg["user"], src_cfg["password"])

    print(f"Connecting to destination: {args.dst_account} ({dst_cfg['host']}:{dst_cfg['port']})")
    dst_conn = imap_backend.connect(dst_cfg["host"], dst_cfg["port"], dst_cfg["user"], dst_cfg["password"])

    skip = {f.lower() for f in SKIP_FOLDERS}
    src_folders = imap_backend.list_folders(src_conn)
    dst_folders = imap_backend.list_folders(dst_conn)

    criteria = imap_backend.build_imap_search_criteria(to=args.to)

    # Search all source folders
    plan: dict[str, list[bytes]] = defaultdict(list)
    total = 0

    print(f"\nSearching for emails TO {args.to}...")
    for folder in sorted(src_folders):
        if folder.lower() in skip:
            continue
        try:
            src_conn.select(f'"{folder}"', readonly=True)
        except Exception:
            continue

        uids = imap_backend.imap_search(src_conn, criteria)
        if uids:
            plan[folder] = uids
            total += len(uids)
            print(f"  {folder}: {len(uids)} email(s)")

        if args.max_results and total >= args.max_results:
            break

    if not total:
        print("\nNo emails found.")
        src_conn.logout()
        dst_conn.logout()
        return

    print(f"\nTotal: {total} email(s) across {len(plan)} folder(s)")

    if args.dry_run:
        print("\nDry run — no emails moved.")
        src_conn.logout()
        dst_conn.logout()
        return

    # Execute moves
    print("\nMoving emails...")
    moved = 0
    failed = 0
    t0 = time.time()

    for folder, uids in sorted(plan.items()):
        # Map source folder to destination folder
        # Proton uses "Folders/X/Y" prefix; strip it for destination
        dst_folder = folder
        for prefix in ("Folders/", "Labels/"):
            if dst_folder.startswith(prefix):
                dst_folder = dst_folder[len(prefix) :]
                break

        # Ensure destination folder exists
        resolved_dst = imap_backend.resolve_folder(dst_folder, dst_folders)
        if resolved_dst not in dst_folders:
            print(f"  Creating folder: {dst_folder} ({resolved_dst})")
            if imap_backend.create_folder(dst_conn, resolved_dst):
                dst_folders.add(resolved_dst)
            else:
                print(f"  ERROR: Could not create {resolved_dst}, skipping")
                failed += len(uids)
                continue

        # Select source folder read-write
        src_conn.select(f'"{folder}"', readonly=False)

        since_expunge = 0
        MICRO_BATCH = 10

        for uid in uids:
            # Fetch full message from source
            msg_bytes = imap_backend.fetch_full_message(src_conn, uid)
            if not msg_bytes:
                failed += 1
                continue

            # Get flags
            flags = imap_backend.fetch_message_flags(src_conn, uid)
            # Clean up flags for APPEND (remove \Recent which is read-only)
            clean_flags = flags.replace(r"\Recent", "").strip()

            # Append to destination
            if imap_backend.append_message(dst_conn, resolved_dst, msg_bytes, clean_flags):
                # Delete from source
                imap_backend.delete_message(src_conn, uid)
                moved += 1
                since_expunge += 1
            else:
                failed += 1

            # Micro-batch: expunge and keepalive to prevent slow-server hangs
            if since_expunge >= MICRO_BATCH:
                try:
                    src_conn.expunge()
                    imap_backend.keepalive(src_conn)
                    imap_backend.keepalive(dst_conn)
                except Exception:
                    pass
                since_expunge = 0

            if moved % 10 == 0 and moved > 0:
                elapsed = time.time() - t0
                rate = moved / elapsed if elapsed > 0 else 0
                print(f"  Progress: {moved}/{total} ({rate:.1f}/s)")

        # Expunge remaining deletions in this folder
        with contextlib.suppress(Exception):
            src_conn.expunge()
        print(f"  {folder} -> {dst_folder}: {len(uids)} email(s)")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s: {moved} moved, {failed} failed")

    src_conn.logout()
    dst_conn.logout()


if __name__ == "__main__":
    main()
