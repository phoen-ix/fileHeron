# file:Heron v1.60.0

**Notifications, alerts, analytics, and webhooks.** The fourth follow-up release
from the code audit. A batch of reliability fixes across email/alert delivery, the
notification bell, analytics, webhooks, and public links. No database migration and
no host step - deploy from this banner.

## What's fixed

- **Operations and inbound-mail email alerts now actually send.** If you set the
  "operations alert" or "inbound message" notification categories to email, they
  were silently doing nothing (no email template existed). They now send a proper
  email to admins.
- **Undeliverable emails no longer sit stuck as "queued".** When email delivery
  fails through all retries, the mail-log row is now marked *failed* and surfaced
  to operations, instead of lingering as "queued" forever.
- **The notification bell catches up after a reconnect.** The live connection
  refreshes about once a minute; notifications that arrived during that brief gap
  are now replayed on reconnect, so the unread badge no longer drifts.
- **Error-alert rate-capping no longer swallows an alert.** When the hourly alert
  cap was hit, the next occurrence of an error could be silently lost and the later
  alert under-counted how many were suppressed. The cap and the cooldown are now
  applied in the right order, so nothing is dropped and counts are accurate.
- **Analytics charts use your timezone.** The daily activity charts (shares,
  downloads, quarantines) now group events by your configured site timezone, so an
  event near midnight lands on the correct day instead of the UTC day.
- **Webhooks don't fire for rolled-back changes.** Outbound webhooks are now sent
  only after the change that triggered them is committed, so a change that gets
  rolled back no longer delivers a "ghost" event.
- **Anomaly detection covers the whole gap between scans.** The detector's lookback
  windows now scale with the scan interval, so a burst that happens between two
  scans is no longer missed.
- **Public links: the last download can finish.** On a one-time (single-download)
  public link, a resumed or ranged download that had already consumed the last
  allowed download now completes instead of being refused.

## Notes

- **No migration, no host step** - the in-app Update swaps the backend, worker, and
  frontend images.
