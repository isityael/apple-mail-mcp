# Search Pattern Reference

Quick-reference for finding emails with Apple Mail MCP search tools.
All search tools use IMAP when configured (faster, no Mail.app freezes).

## By Recipient (IMAP-accelerated)
```
search_emails_advanced(account="proton", to_contains="yael@m0sh1.cc")
search_emails_advanced(account="proton", cc_contains="team@example.com")
```

## By Subject
```
get_email_with_content(account="Work", subject_keyword="meeting", max_results=5)
search_emails(account="Work", subject_keyword="urgent", mailbox="All")
search_emails_advanced(account="Work", subject_contains="invoice")
```

## By Sender
```
search_by_sender(account="Work", sender="boss@company.com", mailbox="All")
get_recent_from_sender(account="Work", sender="client@example.com", count=10)
search_emails_advanced(account="Work", sender_contains="@company.com")
```

## By Date Range
```
search_emails_advanced(account="Work", date_from="2026-01-01", date_to="2026-01-31")
search_emails(account="Work", date_from="2026-01-01", date_to="2026-01-31", mailbox="All")
```

## By Status
```
search_emails_advanced(account="Work", is_read=False)
search_emails_advanced(account="Work", has_attachments=True, is_flagged=True)
```

## Full-Text Body Search
```
search_email_content(account="Work", search_term="invoice number", mailbox="INBOX")
search_emails_advanced(account="Work", body_contains="payment confirmation")
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
get_newsletters(account="Work", days_back=30)
```

## Multi-Criteria (AND logic)
```
search_emails_advanced(
    account="Work",
    sender_contains="client@example.com",
    subject_contains="invoice",
    has_attachments=True,
    date_from="2026-01-01",
    is_read=False,
    mailbox="All"
)
```

## Pagination
```
search_emails_advanced(account="Work", sender_contains="github", max_results=20, offset=0)
search_emails_advanced(account="Work", sender_contains="github", max_results=20, offset=20)
```

## Tips
- Use `mailbox="All"` when location is unknown
- Partial sender match works: `sender_contains="@gmail.com"`
- `search_emails_advanced` is the most powerful — use it for complex queries
- IMAP-configured accounts are searched server-side (fast even on large mailboxes)
- `to_contains` and `cc_contains` only work reliably with IMAP-configured accounts
- Start broad, then add filters to narrow results
