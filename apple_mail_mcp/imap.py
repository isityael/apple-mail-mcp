"""IMAP backend for direct mailbox operations (faster than AppleScript for IMAP accounts)."""

import email
import imaplib
import os
import ssl
from email.header import decode_header, make_header

# Default connection settings (overridable via env vars)
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1143
SOCKET_TIMEOUT = 30  # seconds


CONFIG_FILE = os.path.expanduser("~/.config/apple-mail-mcp/imap.json")


def _load_config_file() -> dict:
    """Load and cache the raw config file contents."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    import json

    with open(CONFIG_FILE) as f:
        return json.load(f)


def get_imap_config() -> dict:
    """Read legacy single-account IMAP config (backward compat).

    Config file (~/.config/apple-mail-mcp/imap.json):
    {"host": "127.0.0.1", "port": 1143, "user": "you@proton.me", "password": "..."}

    Or multi-account format (uses first account):
    {"accounts": {"proton": {"host": ..., "port": ..., "user": ..., "password": ...}}}
    """
    config = {
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
        "user": "",
        "password": "",
    }

    file_config = _load_config_file()

    if "accounts" in file_config:
        # Multi-account format — use first account as legacy default
        accounts = file_config["accounts"]
        if accounts:
            first = next(iter(accounts.values()))
            config.update(first)
    else:
        # Legacy single-account format
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


def get_account_config(account_name: str) -> dict | None:
    """Look up IMAP config for a specific account by name.

    Checks the ``accounts`` dict in imap.json first, then falls back to the
    legacy single-account config if the ``user`` field matches *account_name*.

    Returns None when no IMAP config exists for the account (caller should
    fall back to AppleScript).
    """
    file_config = _load_config_file()

    # Multi-account lookup
    if "accounts" in file_config:
        accounts = file_config["accounts"]
        if account_name in accounts:
            base = {"host": DEFAULT_HOST, "port": DEFAULT_PORT, "user": "", "password": ""}
            base.update(accounts[account_name])
            return base
        # Try case-insensitive match
        lower = account_name.lower()
        for key, val in accounts.items():
            if key.lower() == lower:
                base = {"host": DEFAULT_HOST, "port": DEFAULT_PORT, "user": "", "password": ""}
                base.update(val)
                return base
        return None

    # Legacy single-account — check if user field matches
    legacy = get_imap_config()
    if legacy.get("user") and legacy["user"].lower() == account_name.lower():
        return legacy

    return None


def has_imap_config(account_name: str) -> bool:
    """Return True if IMAP config exists for the given account."""
    return get_account_config(account_name) is not None


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def connect(host: str, port: int, user: str, password: str) -> imaplib.IMAP4:
    """Connect and authenticate to an IMAP server.

    Tries SSL first (Proton Bridge v3 default), then STARTTLS, then plain.
    Plain (unencrypted) fallback is only permitted for loopback addresses.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # Bridge uses a self-signed cert

    imap = None
    # 1. SSL
    try:
        imap = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        imap.socket().settimeout(SOCKET_TIMEOUT)
    except Exception:
        pass

    # 2. STARTTLS
    if imap is None:
        try:
            imap = imaplib.IMAP4(host, port)
            imap.socket().settimeout(SOCKET_TIMEOUT)
            imap.starttls(ssl_context=ctx)
        except Exception:
            imap = None

    # 3. Plain — loopback only
    if imap is None:
        if host not in _LOOPBACK:
            raise ConnectionError(
                f"Cannot connect to {host}: SSL and STARTTLS both failed; "
                "plain IMAP is only allowed for loopback addresses"
            )
        imap = imaplib.IMAP4(host, port)
        imap.socket().settimeout(SOCKET_TIMEOUT)

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
                    uid = _extract_uid(next_item.decode() if isinstance(next_item, bytes) else next_item)

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


def _extract_uid(line: str) -> bytes | None:
    """Extract UID from an IMAP FETCH response fragment like '1 (UID 123 ...' or ' UID 5)'."""
    upper = line.upper()
    idx = upper.find("UID ")
    if idx == -1:
        return None
    rest = line[idx + 4 :].strip()
    uid_str = rest.split()[0].rstrip(")")
    if uid_str.isdigit():
        return uid_str.encode()
    return None


def move_message(imap: imaplib.IMAP4, uid: bytes, destination: str, *, timeout: int = 60) -> bool:
    """Move a single UID to destination folder.

    Uses MOVE (RFC 6851) if available, falls back to COPY+DELETE.
    Sets a per-operation socket timeout to prevent hanging on slow servers.
    """
    dest_quoted = f'"{destination}"'
    old_timeout = imap.socket().gettimeout()
    try:
        imap.socket().settimeout(timeout)
        typ, _ = imap.uid("move", uid, dest_quoted)
        if typ == "OK":
            return True
        # Fallback
        typ, _ = imap.uid("copy", uid, dest_quoted)
        if typ != "OK":
            return False
        imap.uid("store", uid, "+FLAGS", r"(\Deleted)")
        return True
    except (TimeoutError, OSError):
        return False
    finally:
        imap.socket().settimeout(old_timeout)


def keepalive(imap: imaplib.IMAP4) -> bool:
    """Send NOOP to keep the IMAP connection alive. Returns True on success."""
    try:
        typ, _ = imap.noop()
        return typ == "OK"
    except Exception:
        return False


def create_folder(imap: imaplib.IMAP4, name: str) -> bool:
    """Create an IMAP folder. Returns True on success."""
    typ, _ = imap.create(f'"{name}"')
    return typ == "OK"


def fetch_full_message(imap: imaplib.IMAP4, uid: bytes) -> bytes | None:
    """Fetch the full RFC822 message for a single UID.

    Returns the raw message bytes, or None on failure.
    Uses BODY.PEEK to avoid marking the message as read.
    """
    typ, data = imap.uid("fetch", uid, "(BODY.PEEK[] FLAGS)")
    if typ != "OK" or not data:
        return None
    for item in data:
        if isinstance(item, tuple) and len(item) == 2:
            return item[1]
    return None


def fetch_message_flags(imap: imaplib.IMAP4, uid: bytes) -> str:
    """Fetch the IMAP flags for a single UID. Returns flag string like '(\\Seen \\Flagged)'."""
    typ, data = imap.uid("fetch", uid, "(FLAGS)")
    if typ != "OK" or not data:
        return "()"
    raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    start = raw.find("FLAGS (")
    if start == -1:
        return "()"
    end = raw.find(")", start + 7)
    return raw[start + 6 : end + 1] if end != -1 else "()"


def append_message(
    imap: imaplib.IMAP4,
    mailbox: str,
    message: bytes,
    flags: str = "",
) -> bool:
    """Append a raw RFC822 message to a mailbox.

    Args:
        imap: Connected IMAP session.
        mailbox: Destination mailbox name.
        message: Raw RFC822 message bytes.
        flags: Optional IMAP flag string (e.g. '\\Seen').
    """
    typ, _ = imap.append(f'"{mailbox}"', flags, None, message)
    return typ == "OK"


def delete_message(imap: imaplib.IMAP4, uid: bytes) -> bool:
    """Mark a message as deleted by UID. Call expunge() afterwards."""
    typ, _ = imap.uid("store", uid, "+FLAGS", r"(\Deleted)")
    return typ == "OK"


# ---------------------------------------------------------------------------
# IMAP search helpers
# ---------------------------------------------------------------------------

_MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def _iso_to_imap_date(iso_date: str) -> str:
    """Convert 'YYYY-MM-DD' to IMAP date format 'DD-Mon-YYYY'."""
    y, m, d = iso_date.split("-")
    return f"{int(d):02d}-{_MONTHS[int(m) - 1]}-{y}"


def build_imap_search_criteria(
    *,
    to: str | None = None,
    cc: str | None = None,
    from_addr: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    since: str | None = None,
    before: str | None = None,
    unseen: bool | None = None,
    flagged: bool | None = None,
) -> str:
    """Build an IMAP SEARCH criteria string from keyword arguments.

    Date arguments accept ISO format (YYYY-MM-DD) and are converted to
    IMAP's DD-Mon-YYYY format internally.

    Returns a space-separated criteria string suitable for
    ``conn.uid("search", None, criteria)``.  Returns ``"ALL"`` when no
    filters are given.
    """
    parts: list[str] = []

    if to:
        parts.append(f'TO "{to}"')
    if cc:
        parts.append(f'CC "{cc}"')
    if from_addr:
        parts.append(f'FROM "{from_addr}"')
    if subject:
        parts.append(f'SUBJECT "{subject}"')
    if body:
        parts.append(f'BODY "{body}"')
    if since:
        parts.append(f"SINCE {_iso_to_imap_date(since)}")
    if before:
        parts.append(f"BEFORE {_iso_to_imap_date(before)}")
    if unseen is True:
        parts.append("UNSEEN")
    elif unseen is False:
        parts.append("SEEN")
    if flagged is True:
        parts.append("FLAGGED")
    elif flagged is False:
        parts.append("UNFLAGGED")

    return " ".join(parts) if parts else "ALL"


def imap_search(conn: imaplib.IMAP4, criteria: str) -> list[bytes]:
    """Run an IMAP UID SEARCH and return the list of matching UIDs."""
    _, data = conn.uid("search", None, criteria)
    if not data or not data[0]:
        return []
    return data[0].split()


def batch_fetch_headers(
    conn: imaplib.IMAP4,
    uids: list[bytes],
    header_names: tuple[str, ...] = ("From", "To", "Subject", "Date", "Cc"),
) -> list[dict[str, str | bytes]]:
    """Fetch specified headers for a set of UIDs in a single IMAP call.

    Returns a list of dicts with lowercased header keys plus ``uid``.
    This generalises ``batch_fetch_from_headers()`` to arbitrary headers.
    """
    if not uids:
        return []

    fields = " ".join(header_names)
    uid_range = b",".join(uids)
    _, fetch_data = conn.uid("fetch", uid_range, f"(BODY.PEEK[HEADER.FIELDS ({fields})])")

    results: list[dict[str, str | bytes]] = []
    i = 0
    while i < len(fetch_data):
        item = fetch_data[i]
        if isinstance(item, tuple) and len(item) == 2:
            raw_header = item[1]
            meta = item[0].decode() if isinstance(item[0], bytes) else item[0]

            uid = _extract_uid(meta)
            if uid is None and i + 1 < len(fetch_data):
                next_item = fetch_data[i + 1]
                if isinstance(next_item, bytes):
                    uid = _extract_uid(next_item.decode())

            if uid is not None:
                msg = email.message_from_bytes(raw_header)
                record: dict[str, str | bytes] = {"uid": uid}
                for hdr in header_names:
                    raw = msg.get(hdr, "")
                    try:
                        record[hdr.lower()] = str(make_header(decode_header(raw)))
                    except Exception:
                        record[hdr.lower()] = raw
                results.append(record)
        i += 1

    return results
