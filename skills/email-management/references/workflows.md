# Workflow Templates

Ready-to-use workflow patterns for common email management tasks.

## Morning Inbox Check (10 min)

```
get_inbox_overview()
search_emails(subject_keyword="urgent", read_status="unread")
search_emails(sender="boss@company.com", read_status="unread")
update_email_status(action="flag", subject_keyword="action required", max_updates=5)
manage_trash(action="move_to_trash", sender="newsletter@spam.com", max_deletes=10)
```

## End of Day Cleanup (5 min)

```
get_unread_count()
get_recent_emails(account="Work", count=20, include_content=False)
update_email_status(action="mark_read", sender="automated@", max_updates=10)
move_email(to_mailbox="Archive", from_mailbox="INBOX", max_moves=20)
```

## Quick Reply and Archive

```
get_email_with_content(account="Work", subject_keyword="Quick Question", max_results=1)
reply_to_email(account="Work", subject_keyword="Quick Question", reply_body="Yes, that works!")
move_email(account="Work", subject_keyword="Quick Question", to_mailbox="Archive", max_moves=1)
```

## Deferred Response (Draft)

```
get_email_with_content(account="Work", subject_keyword="Complex Request", max_results=1)
manage_drafts(account="Work", action="create", subject="Re: Complex Request", to="sender@example.com", body="Initial thoughts...")
update_email_status(account="Work", action="flag", subject_keyword="Complex Request", max_updates=1)
```

## Forward with Context

```
get_email_with_content(account="Work", subject_keyword="Customer Issue", max_results=1)
forward_email(account="Work", subject_keyword="Customer Issue", to="colleague@company.com", message="Can you help with this?")
move_email(account="Work", subject_keyword="Customer Issue", to_mailbox="Waiting For", max_moves=1)
```

## Bulk Organization by Sender

```
search_emails(account="Work", sender="client@example.com", mailbox="INBOX", max_results=50)
bulk_move_emails(account="Work", sender="client@example.com", to_mailbox="Clients/ClientName", from_mailbox="INBOX")
```

## IMAP Rule-Based Sorting

```
sort_inbox()  # Uses rules from ~/.config/apple-mail-mcp/sort_rules.json
imap_bulk_move(source_folder="OldFolder", dest_folder="Archive/OldFolder")  # Merge folders
```

## Newsletter Cleanup

```
get_newsletters(account="Work", days_back=30, min_emails=3)
manage_trash(action="move_to_trash", sender="newsletter@unwanted.com", max_deletes=20)
```

## Weekly Analytics Review

```
get_statistics(account="Work", scope="account_overview", days_back=7)
list_mailboxes(account="Work", include_counts=True)
manage_drafts(account="Work", action="list")
```

## Export and Backup

```
export_emails(account="Work", scope="entire_mailbox", mailbox="Important", save_directory="~/Documents/Email-Backup", format="txt")
list_email_attachments(account="Work", subject_keyword="Contract")
save_email_attachment(account="Work", subject_keyword="Contract", attachment_name="contract.pdf", save_path="~/Documents/contract.pdf")
```

## Vacation Recovery (200+ unread)

```
get_inbox_overview()
get_statistics(scope="account_overview", days_back=14)
manage_trash(action="move_to_trash", sender="newsletter@", max_deletes=20)  # cull noise
update_email_status(action="mark_read", sender="no-reply@", max_updates=20)  # mark automated
search_emails(sender="boss@company.com", read_status="unread")  # check VIPs
search_emails(subject_keyword="urgent", read_status="unread")  # check urgent
```

## Folder Structure Decision

Use folders when:
- 5+ emails/week in that category
- You'll actively file into it
- Category is long-term (3+ months)

Otherwise, rely on search. Keep structure to max 2-3 levels deep.
