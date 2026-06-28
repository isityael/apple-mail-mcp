"""Shared constants for Apple Mail MCP tools."""

# Newsletter detection patterns (sender-based)
NEWSLETTER_PLATFORM_PATTERNS = [
    "substack.com",
    "beehiiv.com",
    "mailchimp",
    "sendgrid",
    "convertkit",
    "buttondown",
    "ghost.io",
    "revue.co",
    "mailgun",
]

NEWSLETTER_KEYWORD_PATTERNS = [
    "newsletter",
    "digest",
    "weekly",
    "daily",
    "bulletin",
    "briefing",
    "news@",
    "updates@",
]

# Folders to skip during broad searches
SKIP_FOLDERS = [
    "Trash",
    "Junk",
    "Junk Email",
    "Deleted Items",
    "Sent",
    "Sent Items",
    "Sent Messages",
    "Drafts",
    "Spam",
    "Deleted Messages",
    "All Mail",
    "All Messages",
    "Corbeille",
    "Courrier indésirable",
    "Indésirables",
    "Éléments supprimés",
    "Éléments envoyés",
    "Messages envoyés",
    "Brouillons",
    "Boîte d'envoi",
    "Papierkorb",
    "Gesendet",
    "Entwürfe",
    "Werbung",
    "Papelera",
    "Enviados",
    "Borradores",
    "Correo no deseado",
]

FLAG_COLORS = {
    "red": 0,
    "orange": 1,
    "yellow": 2,
    "green": 3,
    "blue": 4,
    "purple": 5,
    "gray": 6,
    "grey": 6,
}

FLAG_COLOR_NAMES = {index: name for name, index in FLAG_COLORS.items() if name != "grey"}

# Thread subject prefixes to strip when matching threads
THREAD_PREFIXES = ["Re:", "Fwd:", "FW:", "RE:", "Fw:"]

# Human-friendly time range mappings (name -> days)
TIME_RANGES = {
    "today": 1,
    "yesterday": 2,
    "week": 7,
    "month": 30,
    "all": 0,
}
