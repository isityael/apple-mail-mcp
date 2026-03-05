# Search Pattern Reference

Quick-reference for finding emails with Apple Mail MCP search tools.

## By Subject
```
get_email_with_content(account="Work", subject_keyword="meeting", max_results=5)
search_emails(account="Work", subject_keyword="urgent", mailbox="All")
```

## By Sender
```
search_by_sender(account="Work", sender="boss@company.com", mailbox="All")
get_recent_from_sender(account="Work", sender="client@example.com", count=10)
search_emails(account="Work", sender="@company.com", mailbox="All")  # domain match
```

## By Date Range
```
search_emails(account="Work", date_from="2025-01-01", date_to="2025-01-31", mailbox="All")
```

## By Status
```
search_emails(account="Work", read_status="unread", mailbox="INBOX")
search_emails(account="Work", has_attachments=True, mailbox="All")
```

## Full-Text Body Search
```
search_email_content(account="Work", search_term="invoice number", mailbox="INBOX")
```

## Cross-Account
```
search_all_accounts(subject_keyword="quarterly report", max_results=20)
```

## Conversations
```
get_email_thread(account="Work", subject_keyword="Project Alpha", mailbox="All", max_messages=50)
```

## Newsletter Discovery
```
get_newsletters(account="Work", days_back=30, min_emails=3)
```

## Multi-Criteria (AND logic)
```
search_emails(
    account="Work",
    sender="client@example.com",
    subject_keyword="invoice",
    has_attachments=True,
    date_from="2025-01-01",
    read_status="unread",
    mailbox="All"
)
```

## OR Logic (run multiple searches)
```
search_emails(account="Work", subject_keyword="urgent", read_status="unread")
search_emails(account="Work", subject_keyword="ASAP", read_status="unread")
```

## Common Urgency Searches
```
search_emails(subject_keyword="urgent")
search_emails(subject_keyword="action required")
search_emails(subject_keyword="deadline")
search_emails(subject_keyword="please review")
search_emails(subject_keyword="approval")
```

## Tips
- Use `mailbox="All"` when location is unknown
- Partial sender match works: `sender="@gmail.com"`
- `include_content=True` is slower — limit `max_results` when using it
- Start broad, then add filters to narrow results
