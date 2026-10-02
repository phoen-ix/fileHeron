# Inbound mail (IMAP)

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `backend/app/services/{imap_client,imap_config,imap_poll,inbound_mail,inbound_parse,inbound_classify}.py`, `backend/app/workers/{imap_poll,rescan_inbound_attachments}.py`, `backend/app/routers/admin/imap.py`

## Inbound IMAP

Services `imap_{client,config,poll}.py` + `inbound_{mail,parse,classify}.py`;
workers `imap_poll` + `rescan_inbound_attachments`; admin `/admin/inbox` +
`/admin/settings/imap`.

- **No anonymous senders:** `imap.require_known_sender` (default **true**, admin-tunable) refuses mail whose From matches no enabled user, *before anything is written* - the policy was documented for four releases while nothing enforced it. Refused mail is left on the server, counted as `refused_unknown_sender`.
- **Cadence/enabled moved to the cron scheduler** - `run_poll` only feature-gates on `imap.enabled`, it does not self-schedule.
- **IMAP TLS verifies** (`imap_client._tls_context`) - both modes previously accepted any certificate, and `uses_smtp_credentials` defaults true, so the LOGIN carried the org's outbound-mail password. `imap.tls_insecure` (default off) is the escape hatch. Mailbox names are QUOTED (`_mbox`) and CR/LF refused; `delete()` uses UID EXPUNGE; **a failed MOVE raises** rather than falling through to a delete.
- **A connect failure must name every resolved address.** `imaplib` connects via `socket.create_connection`, which walks all `getaddrinfo` results and re-raises only the LAST one's exception - so on a dual-stack host the reported errno belongs to whichever family sorts last, not to the leg that matters (most of the reference instance's poll failures blamed an unreachable IPv6 leg and pointed away from the real fault). `imap_client._connect_failure` names the host, every address, and which one the errno came from; `ssl.SSLError` is re-raised untouched so a TLS fault keeps its own message.
- **Dedup by `(uidvalidity, imap_uid)` ONLY.** `message_id` was removed as a vulnerability, not simplified away: it comes verbatim off the wire, so a forged value made a later genuine mail look like a duplicate and the poll advanced its highwater past it - **mail silently destroyed**. It survives as an advisory `message_id_seen_before` that only logs. A UIDVALIDITY change resets `last_uid` to 0. Post-fetch server action applies **only after successful ingest+commit**.
- **Attachments are clamd-scanned inline before landing anywhere servable.** clamd down → store the attachment `pending` (download-gated) and CONTINUE - **never let `AVUnavailableError` propagate**, or the poll aborts, the UID highwater never advances, and ALL inbound ingestion stalls permanently on a single mail. `rescan_inbound_attachments` re-scans `pending` after an outage.
- **The rescan's deferral list is a fixed ZSET, `fh:inbound:rescan:deferred`, scored by the latest failure and pruned per member** - finding deferred ids was a SCAN over `fh:inbound:rescan:fail:*`, and this Redis may be shared. `tests/test_no_keyspace_walk.py` fails any `scan`/`scan_iter`/`keys(pattern)` call anywhere in `app/`; a reader that needs "every key like X" keeps a fixed index key.
- **`imap_poll.MAX_MESSAGE_BYTES` is checked via `RFC822.SIZE` BEFORE the fetch** - downloading the message is what OOM-kills the worker. Also bounded: `MAX_MESSAGE_PARTS`, `MAX_ATTACHMENTS_PER_MESSAGE`, `MAX_MESSAGES_PER_RUN`, `_MAX_BODY_TOTAL`; the poll lock outlives the ARQ job timeout.
- **Every String-column field is truncated to its length at ingest** - an over-long header otherwise raises DataError under MariaDB strict mode and re-wedges the poll. `inbound_classify.classify` is header-only + pure and decodes RFC2047 subjects before matching auto-reply hints.
