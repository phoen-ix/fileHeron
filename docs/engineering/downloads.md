# Downloads: budgets, transfer marks, bulk ZIP

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `backend/app/routers/{files,public}.py`, `backend/app/services/{transfer_activity,download_token,zip_stream,zip_writer,preview}.py`, `backend/app/utils/http_range.py`, `client/src/fileheron_client/api/download_*.py`

## Downloads: budgets + transfer marks

**There are TWO marks and they answer different questions. Never point a budget
at the serving mark.**

| mark | question | used by | TTL | on Redis failure |
|---|---|---|---|---|
| `transfer_activity.was_download_recent` | "did this instance serve bytes for this recently" | the maintenance DRAIN, and the encryption backfill's "leave it for later" (never a budget) | 30 min | fails **OPEN** |
| `transfer_activity.was_download_paid` | "has THIS PRINCIPAL already paid" | budgets only | `PAID_TTL_SEC`, **2 h** | fails **CLOSED** |

The paid mark is written ONLY where the counter moves and is keyed on the payer
(`link:{id}:...`). Using the serving mark for both let an owner previewing their
own file buy every link holder unlimited free downloads, let the two ZIP routes
corroborate each other across the auth boundary, and let a free continuation
refresh its own licence indefinitely. **2 h is not 12 h** - the module records
having deliberately REJECTED 12 h as "a day pass". An AUDIT trail uses the paid
mark too, with the opposite bias: when in doubt, WRITE the row.

- **Never write a bare `if is_partial_continuation(request)` around a counter, a log write or a state check. And never charge a ranged download on WHERE it starts - charge on HOW MUCH it takes.** The desktop client opens every transfer with `Range: bytes=1-1` to learn the size; charging that probe made a `download_limit=1` share undownloadable from the client while a browser still worked. `utils/http_range.is_metadata_probe` is the exemption and `PROBE_MAX_BYTES` is 1 on purpose - the slack is what an extraction attack would spend. **The exemption is pinned to `PROBE_OFFSET` as well as the length**: bounding only the LENGTH let `bytes=i-i` walk a whole file out for free.
- **The header is a claim; every exemption pairs it with evidence** - `was_download_paid(key)` on the anonymous paths, `file.has_recent_counted_download(...)` windowed by `downloads.resume_credit_hours` on the authenticated ones (durable across a Redis restart; the desktop client's overnight pause needs it). The authenticated ZIP corroborates on `user:{id}:zip:{share}:{etag}`, never on a `download_log` row.
- **`share.is_review_access()` is the ONE definition both download routes consult** for whether an access is charged. P10 widened WHO may reach a share's bytes (a non-recipient approver, on an ACTIVE share carrying files awaiting review) while both routes still read `is_review = share.state == pending_approval` and the budget branch keyed on `state == active`, so the approver paid from the recipients' budget and a `download_limit=1` share was exhausted before a recipient fetched anything. An approver who is also a recipient still pays. **Test this with a NON-ADMIN approver**: an admin passes `is_authorized_to_download` outright and never reaches the branch, which is why it survived.
- **Signed download URL:** `<a href>` can't carry a bearer, so `GET /api/files/{id}/download-url` issues a short-lived HMAC token consumed via `?dt=` (ungated `download_router` for `?dt=`, gated `router` for bearer). TTL admin-tunable `downloads.signed_url_ttl_sec` (default 900s) so a browser's native Resume revalidates the same URL; `verify()` reads `exp` from the token, so only mint reads the setting.
- **`active_downloads()` must return None, not 0, when Redis cannot answer** - a 0 makes the drain conclude the stack is idle and fire a postponed update straight into live downloads. The deadline still bounds the wait.

## Bulk ZIP download

`services/zip_stream.py`: mint `GET /api/files/{share_id}/download-zip-url` →
consume `…/download-zip?dt=`; public `GET /api/public/{token}/download-zip`.

- **ZIP_STORED, streamed, never cached to disk** - a cached archive would double bytes on the bind mount and dodge expiry/GDPR-delete. Sized mode (`ZipStream(sized=True)`) gives an exact Content-Length up front (browser progress + Range resume) while streaming member bytes lazily.
- **`safe_arcname()` sanitises member names** - `zipstream-ng.add_path` does not, so a stored `../../etc/passwd` name would land verbatim. Strips dir components/nulls, de-dupes `(n)`.
- One `downloads_remaining` decrement per ZIP (not per member). `count=True` registers the stream in `transfer_activity` for the drain; decremented in the generator `finally` (fires on mid-stream disconnect). The S3 path passes an explicit `size=`.
- **The archive is resumable, so its bytes are load-bearing.** `SizedZipStream` must stay reproducible (caller-supplied `mtime`, `time.gmtime` not `localtime`) and `file.downloadable_files` must keep its `File.id` tiebreaker, or a resume splices two different archives. `iter_from(0)` IS the full stream - one code path on purpose. A member behind the resume point needs its CRC from `fh:zip:crc:{file_id}` or a re-read; if that would cost more than `zip_stream.MAX_RESUME_REREAD_BYTES` the route serves a **200 full archive**. **Never emit a guessed CRC**, and never cache the partial CRC of a window that closed mid-member. **`LAYOUT_VERSION` must be bumped when the produced bytes change** - it is in the ETag, which is what makes an in-flight `If-Range` restart instead of corrupt. `share.has_recent_archive_download` is the durable half of resume evidence.
