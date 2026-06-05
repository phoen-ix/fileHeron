# file:Heron v1.11.0

**New: Mail log.** file:Heron now keeps a record of every email it sends, so you
can confirm a user received their share notice / invite / password-reset, see
exactly what was sent, and diagnose delivery problems — all from a new admin
page at **Admin → Mail log**.

### What you get

- **A searchable, filterable list** of every outbound email — by recipient,
  category, status, or date. Each row shows the delivery outcome
  (queued → sent / failed) with the SMTP error code when something went wrong.
- **Full-content detail view.** Click any email to see its headers, status, SMTP
  result, and body. The plain-text body is shown inline; an *Open HTML version*
  button renders the HTML email in a new tab.
- **"Emails to this user" panel** on each user's detail page, plus a recipient
  filter on the log — the quickest way to answer "did this person get it?".
- **Resend** any ordinary notification straight from the log.
- **CSV export** of the filtered rows, like the audit log.

### Privacy & safety

- **One-time auth links are redacted at rest.** Password-reset, invite, and
  verify emails have their single-use token masked in the stored copy, so the
  log can never be used to take over an account. Resend is therefore disabled
  for those emails (their token is gone) and enabled for ordinary notifications.
- **90-day retention** by default — admin-tunable via the new
  `retention.email_log_days` setting (Advanced settings); set to 0 to keep
  forever. The nightly cleanup prunes older rows.
- **GDPR-aware:** erasing a user scrubs their mail-log rows (recipient address
  and bodies removed) while preserving aggregate delivery counts.

### Notes

- One DB migration adds the `email_log` table (applied automatically on update).
- New optional env var `EMAIL_LOG_RETENTION_DAYS` (default 90); everything is
  also tunable live in the admin UI.
- Delivery tracking records that the message was **accepted by your SMTP
  server** — it does not track inbox delivery, bounces, or opens.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.11.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.11.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.11.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.11.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.11.0`

Click **Update** in `/admin/system` to roll forward.
