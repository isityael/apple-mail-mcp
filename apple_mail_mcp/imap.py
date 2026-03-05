"""IMAP backend for direct mailbox operations (faster than AppleScript for IMAP accounts)."""

import email
import imaplib
import os
import ssl
import socket
from email.header import decode_header, make_header
from typing import Optional


# Default connection settings (overridable via env vars)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1143
SOCKET_TIMEOUT = 30  # seconds


CONFIG_FILE = os.path.expanduser("~/.config/apple-mail-mcp/imap.json")


def get_imap_config() -> dict:
    """Read IMAP config from config file, falling back to environment variables.

    Config file (~/.config/apple-mail-mcp/imap.json):
    {"host": "127.0.0.1", "port": 1143, "user": "you@proton.me", "password": "..."}
    """
    config = {
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
        "user": "",
        "password": "",
    }

    # Try config file first
    if os.path.exists(CONFIG_FILE):
        import json
        with open(CONFIG_FILE) as f:
            file_config = json.load(f)
        config.update(file_config)

    # Env vars override config file
    if os.environ.get("PROTON_BRIDGE_HOST"):
        config["host"] = os.environ["PROTON_BRIDGE_HOST"]
    if os.environ.get("PROTON_BRIDGE_PORT"):
        config["port"] = int(os.environ["PROTON_BRIDGE_PORT"])
    if os.environ.get("PROTON_BRIDGE_USER"):
        config["user"] = os.environ["PROTON_BRIDGE_USER"]
    if os.environ.get("PROTON_BRIDGE_PASSWORD"):
        config["password"] = os.environ["PROTON_BRIDGE_PASSWORD"]

    return config


def connect(host: str, port: int, user: str, password: str) -> imaplib.IMAP4:
    """Connect and authenticate to an IMAP server.

    Tries SSL first (Proton Bridge v3 default), then STARTTLS, then plain.
    """
    socket.setdefaulttimeout(SOCKET_TIMEOUT)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # Bridge uses a self-signed cert

    imap = None
    # 1. SSL
    try:
        imap = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
    except Exception:
        pass

    # 2. STARTTLS
    if imap is None:
        try:
            imap = imaplib.IMAP4(host, port)
            imap.starttls(ssl_context=ctx)
        except Exception:
            imap = None

    # 3. Plain (localhost only)
    if imap is None:
        imap = imaplib.IMAP4(host, port)

    imap.login(user, password)
    return imap


def list_folders(imap: imaplib.IMAP4) -> set[str]:
    """Return a set of existing folder names (raw IMAP names)."""
    _, folders = imap.list()
    existing = set()
    for f in folders:
        if f:
            decoded = f.decode() if isinstance(f, bytes) else f
            parts = decoded.split('"')
            if len(parts) >= 2:
                existing.add(parts[-2].strip())
    return existing


def _encode_imap_utf7(name: str) -> str:
    """Encode a folder name using IMAP modified UTF-7 (RFC 3501).

    In IMAP modified UTF-7, '&' is encoded as '&-'.
    """
    return name.replace("&", "&-")


def resolve_folder(name: str, existing: set[str]) -> str:
    """Resolve a human-readable folder name to its IMAP path.

    Proton Bridge prefixes custom folders with 'Folders/' and labels with 'Labels/'.
    This tries the name as-is first, then with common prefixes.
    Also handles IMAP modified UTF-7 encoding (e.g. '&' → '&-').
    """
    # Try each candidate both as-is and with IMAP UTF-7 encoding
    candidates = [name, f"Folders/{name}", f"Labels/{name}"]
    for candidate in candidates:
        if candidate in existing:
            return candidate
        encoded = _encode_imap_utf7(candidate)
        if encoded != candidate and encoded in existing:
            return encoded

    # No match — return with Folders/ prefix and encoding (for creation)
    return _encode_imap_utf7(f"Folders/{name}")


def batch_fetch_from_headers(imap: imaplib.IMAP4) -> list[tuple[bytes, str]]:
    """Fetch From headers for ALL messages in the currently selected mailbox.

    Returns list of (uid, from_header) tuples.
    Much faster than fetching one-by-one (~50x for large mailboxes).
    """
    _, data = imap.uid("search", None, "ALL")
    if not data or not data[0]:
        return []

    uids = data[0].split()
    if not uids:
        return []

    # Batch fetch all From headers in one IMAP call
    uid_range = b",".join(uids)
    _, fetch_data = imap.uid("fetch", uid_range, "(BODY.PEEK[HEADER.FIELDS (FROM)])")

    results = []
    # Proton Bridge returns fetch data as:
    #   (b'1 (BODY[HEADER.FIELDS (FROM)] {55}', b'From: ...\r\n'),
    #   b' UID 5)',
    #   (b'2 (BODY[HEADER.FIELDS (FROM)] {50}', b'From: ...\r\n'),
    #   b' UID 23)',
    # The UID is in the trailing bytes item after each tuple.
    i = 0
    while i < len(fetch_data):
        item = fetch_data[i]
        if isinstance(item, tuple) and len(item) == 2:
            raw_header = item[1]
            meta = item[0].decode() if isinstance(item[0], bytes) else item[0]

            # Try to get UID from the meta line first
            uid = _extract_uid(meta)

            # If not in meta, check the next item (Proton Bridge style)
            if uid is None and i + 1 < len(fetch_data):
                next_item = fetch_data[i + 1]
                if isinstance(next_item, bytes):
                    uid = _extract_uid(
                        next_item.decode() if isinstance(next_item, bytes) else next_item
                    )

            if uid is not None:
                msg = email.message_from_bytes(raw_header)
                from_val = msg.get("From", "")
                try:
                    from_decoded = str(make_header(decode_header(from_val))).lower()
                except Exception:
                    from_decoded = from_val.lower()
                results.append((uid, from_decoded))
        i += 1

    return results


def _extract_uid(line: str) -> Optional[bytes]:
    """Extract UID from an IMAP FETCH response fragment like '1 (UID 123 ...' or ' UID 5)'."""
    upper = line.upper()
    idx = upper.find("UID ")
    if idx == -1:
        return None
    rest = line[idx + 4:].strip()
    uid_str = rest.split()[0].rstrip(")")
    if uid_str.isdigit():
        return uid_str.encode()
    return None


def move_message(imap: imaplib.IMAP4, uid: bytes, destination: str) -> bool:
    """Move a single UID to destination folder.

    Uses MOVE (RFC 6851) if available, falls back to COPY+DELETE.
    """
    dest_quoted = f'"{destination}"'
    typ, _ = imap.uid("move", uid, dest_quoted)
    if typ == "OK":
        return True
    # Fallback
    typ, _ = imap.uid("copy", uid, dest_quoted)
    if typ != "OK":
        return False
    imap.uid("store", uid, "+FLAGS", r"(\Deleted)")
    return True


def create_folder(imap: imaplib.IMAP4, name: str) -> bool:
    """Create an IMAP folder. Returns True on success."""
    typ, _ = imap.create(f'"{name}"')
    return typ == "OK"
