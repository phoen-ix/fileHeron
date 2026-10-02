# Error log, alerts, CSP, anomaly detection, analytics, webhooks

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `backend/app/services/{error_log,error_alert,alert_dedup,anomaly,analytics,webhook}.py`, `backend/app/middleware/errors.py`, `backend/app/routers/telemetry.py`, `backend/app/routers/admin/{errors,analytics,webhooks}.py`, `backend/app/workers/{notify_admin_error,anomaly_check,analytics_aggregate,webhook_deliver,ops_check}.py`, `docker/frontend/nginx.conf`

## Error log + alerts + CSP

Browsable server-error log + (separately) email alerts. Admin page "Errors & alerts" (System):
tabs `/admin/error-log` (Log) and `/admin/settings/error-alerts` (Alerts). → README §Error log & alerts.

- **Log ≠ alert (decoupled).** The `notify_admin_error` ARQ job → `error_alert.handle_error_event` **LOGS first** (`services/error_log.py::record`) then runs the alert saferails. `error_log.enabled` (default **true**, 5xx + cron failures) is independent of `error_alert.enabled` (default **false**, emails); cooldown/hourly-cap/dedup-signature govern **emails only**. Don't re-couple them.
- **Worker-source alerting has a GLOBAL default (`error_alert.source_worker`, true) plus the per-task `cron.<name>.alert_on_failure` override, and the per-task flag wins either way.** The per-task flag used to be the only control and defaults OFF, so the settings page read "alerting enabled" while every worker failure went unreported. `cron_schedule.effective` reads the SAME default so the Scheduled-tasks page cannot render every task "off" on a page whose failures do alert. **`source_worker` is optional on the PUT (`None` = leave unchanged)** - a newly-required field 422s any client one release behind, the same reasoning `APIBaseModel` keeps `extra="ignore"` for.
- **The scheduled checks' admin-alert dedup is ONE helper, `services/alert_dedup.py`** (disk_check, anomaly_check, ops_check, cron_tracker): an atomic `SET NX EX`, and while Redis raises an in-process record answers, like the login limiter's fallback. Each copy used to return "not seen" on a Redis error, so an outage re-sent every alert hourly - `redis_unhealthy` included - and anomaly_check duplicated its audit rows; "treat as seen" would silence the alert that Redis is down. conftest's `_isolate_alert_dedup` keeps tests off the live fixed keys.
- **`ops_check`'s ClamAV check retries before it alerts** (`_AV_PING_ATTEMPTS` = 3, `_AV_PING_RETRY_DELAY_SEC` = 10). clamd stops answering for several seconds while it activates freshclam's daily signature update - at a time anchored to the clamav container's start, drifting a few seconds a day - and on 2026-10-01 and -02 that window held the hourly check's single ping at 13:04:01, so every admin was mailed `av_unhealthy` about a daemon that never went down. Don't drop back to one probe: a real outage outlasts the retries, a reload does not. The ping runs in a thread and the waits are `asyncio.sleep`, so an outage cannot freeze the worker's event loop. Pinned by `test_ops_check_av_retry.py`.
- **`scripts/send_ops_alert.py` does NOT go through `handle_error_event`** - it enters at the recipient-fanout half, so a backup/restore-drill failure emails admins and writes no `error_log` row. A delivered `RESTORE_DRILL_FAILED` mail is not evidence that in-app alerting works.
- **4xx is opt-in + allowlist-gated.** `error_log.capture_4xx` + `error_log.http_4xx_codes` (CSV of HTTP statuses; **empty allowlist = capture nothing**). `error_alert.source_http_4xx` rides the same allowlist (alert ⊆ capture). `errors.py::_maybe_enqueue_error_event` gates the 4xx enqueue on the **process-cached** `error_log.capture_4xx_enabled_cached()` (~60s TTL; `error_alert.update_settings` resets it); the worker re-checks the allowlist authoritatively. 5xx always enqueue. Flood pre-guards: `err_alert_enqueue` 30/60s; the 4xx rate is the admin-tunable `error_log.scan_capture_per_min` (default 300).
- **Framework HTTPException:** `errors.py::http_exception_handler` funnels route-not-found **404/405** through the capture path AND returns the standard envelope. **422** is `RequestValidationError` (a different type) - stays FastAPI's `{detail:[...]}`, **not** captured; don't "fix" that as a bug.
- **`TOKEN_EXPIRED` is never captured, and the reason is structural.** The SPA refreshes REACTIVELY, and the bell's SSE loop re-mints a stream token every ~61.5s (60s server close + 1500ms backoff), making it the **only timer-driven authenticated request in the product** - so it is always the request that trips the expiry boundary: exactly one 401 per access-token lifetime per open tab, forever, always followed within the second by a successful refresh and replay. Suppressed by **CODE, not status** (`_NEVER_CAPTURE_CODES`, with `JOB_NOT_FOUND`) - `AUTH_REQUIRED` and friends keep capturing, which is what surfaced the ungated admin SSE route. Accepted cost: a genuine MASS expiry (host clock skew) no longer lands here. The viewer's filters (`services/error_log.py::filtered_query`) are include-only, so filtering could not handle it. The backend returns `expires_in_seconds` and the frontend reads it nowhere, which is why the refresh is reactive at all.
- **Edge scanner detection.** `docker/frontend/nginx.conf` routes scanner-bait paths (a curated script/config/vcs **extension** denylist + dotfiles except `/.well-known/`) to the backend → 404 → logged. This is the **only** way edge scans surface: the SPA fallback 200s unknown *page* paths and scanners don't run the SPA JS. nginx.conf is baked into the frontend image → ships via in-app Update (no host step). Per-IP `limit_req zone=probe`.
- **SPA 404 beacon.** `POST /api/telemetry/page-404` reports client-side 404s so they land alongside edge/backend ones. Anonymous + opt-in (no-op unless 4xx capture is on), 10/60s per-IP, query string stripped, rows are `source="spa"`, logged never emailed. Client-asserted (spoofable) by design - bounded by the gate + rate limit. **`/api/telemetry/*` is capped at 64k at the edge** with a `Content-Length` pre-check, because the beacons buffered the body before capping it.
- **The CSP is Report-Only, with a sink at `/api/telemetry/csp-report`;** enforcing it is a deliberate later step, after the reports come back empty. **CSP reports ride `error_log.enabled` (default ON), never `error_log.capture_4xx` (default OFF)** - gating them on the 4xx switch made the policy's own exit criterion satisfiable by a policy never exercised. **The SPA shell only gets the Report-Only policy** (from nginx); the ENFORCING policy covers backend responses only, so `v-html` in `LegalPage.vue` is guarded by nh3 **alone**, and `e2e/tests/legal-page-xss.spec.ts` is the only test that loads a legal page in a real browser.
- **`error_log` table:** `ip` is the address as resolved AT THE BACKEND - a request reaching nginx without an `X-Forwarded-For` (straight to the loopback-published port rather than through Traefik) lands nginx's own peer, the docker bridge gateway; host-local by construction, not a defect, not to be blanked, and never mis-blocked because `is_blockable` refuses every non-global address. No FK on `user_id` (forensic), `signature` for grouping, `alerted` flag. Pruned by `prune_history` + `error_log.retention_days`. The server_error email is admin-only `NotificationCategory.server_error`.

## Anomaly detection

`services/anomaly.py` + hourly `anomaly_check`. **Advisory only - it alerts an
admin and never blocks.** There is no wiring from a Finding to the scan guard,
and there never was; `scan_guard.signal_auth_failure` is a middleware
classification over credential-endpoint 401/403s and cannot see a Finding.

- Admin page `/admin/settings/anomaly` (Security & audit) renders the four thresholds through the registry writer. GeoIP-free: `multi_network` approximates impossible-travel with `utils/geohash.ip_geohash5` - an IP-prefix hash, **NOT geography**.
- **`login_stuffing` needs >threshold failures across ≥3 distinct emails from one IP, and excludes a source that ALSO logged in successfully in the window** - a stuffer never gets in while a NAT'd office does it constantly. Thresholds env-tunable (`ANOMALY_*`); feeds webhooks.
- **Detector lookback windows SCALE with the cron cadence** - `anomaly_check` adds `_WINDOW_OVERLAP_MIN` to the effective cadence and the module constants are FLOORS, so consecutive scans leave no gap.

## Analytics

`services/analytics.py` + daily `analytics_aggregate` + `/admin/analytics`
(hand-rolled SVG via `useAnalyticsCharts`).

- **Only the storage/file-state trend is persisted** (one nightly `analytics_snapshots` row - the only figure deletes destroy); every other panel is computed live. `snapshot_storage_today` is idempotent on `snapshot_date`.
- **`_STORED_STATES` is `quota.STORED_STATES`, IMPORTED** - not a mirror to keep in lockstep. `metrics.py` and `quota_reconcile.py` import it too; `quota_reconcile` is the authoritative DB sum that CORRECTS the Redis counter, so an inline copy would have the reconciler fighting the enforcer hourly. `test_write_before_commit.py` scans **every** module for an inline copy. **`cleanup_stale_uploads`'s `_USABLE_FILE_STATES` is exempt BY NAME and must stay so**: "does any file keep this share active" only coincides with "does this file occupy storage" today, and forcing one symbol would make a state that does one but not the other inexpressible.
- `top_uploaders`/`top_shares` exclude GDPR-erased rows; `func.date()` bucketing for SQLite(tests)+MariaDB(prod).

## Webhooks

`services/webhook.py::emit` → worker `workers/webhook_deliver.py`; models
`Webhook` + `WebhookDelivery`.

- **`emit` never writes the delivery row** - the caller's transaction is uncommitted; the WORKER creates and owns `webhook_deliveries` from the enqueued args. `emit` is best-effort and never raises into the originating action. `services/audit.py` defers the `emit` call to `run_after_commit`, so a rollback drops it instead of delivering an event for a change that never happened.
- Worker **self-re-enqueues** with backoff `{1:5,2:15,3:30,4:60}`s (max 5), NOT ARQ's generic retry (which would lose the row).
- **SSRF re-validated per delivery attempt** (`utils/net.py::assert_public_http_url`) - the create-time check alone is bypassable via config-backup import. `follow_redirects=False`. Signature `X-Webhook-Signature: sha256=<hmac>` over sorted-keys compact JSON; secret Fernet-encrypted.
