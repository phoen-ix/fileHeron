# Scan guard and IP blocks

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `backend/app/middleware/scan_guard.py`, `backend/app/services/scan_guard.py`, `backend/app/utils/{client_ip,geohash}.py`, `backend/app/models/ip_block.py`, `backend/app/routers/admin/scan_guard.py`, `backend/scripts/unblock_ip.py`, the probe limiter in `docker/frontend/nginx.conf`

## Scan guard + IP blocks

Auto-detect and temporarily block scanning sources. Admin page "Blocked sources"
(Security & audit): tabs `/admin/ip-blocks` (STATE - blocks, allowlist, watchlist)
and `/admin/settings/scan-guard` (POLICY, labelled "Auto-block rules (Scan
guard)"). The state page is the item and the guard is its tab, deliberately: the
guard ships OFF and the blocks page is the one an operator opens in an incident.
→ README §Scan guard for the operator view.

**It is the only control in this product that DENIES service, so it ships OFF**
(`scan_guard.enabled` default false) and is defined by what it refuses to do.

### The refusals

- **Never count or block a non-`is_global` address** (`utils/client_ip.py::is_blockable`). The backend has FIVE peers, and bait paths arrive via the frontend **nginx**, not Traefik: pin `FORWARDED_ALLOW_IPS` to the proxy CIDR (as `docker/traefik/README.md` and CLAUDE.md §Operational gotchas both advise) and uvicorn stops honouring XFF from nginx, so every scanner request resolves to *nginx's own container address* - one source, 100% of the 404s, maximum path diversity, a textbook scanner - and blocking it takes `/api/` down for the whole SPA. The same refusal covers the bridge gateway, tusd, the updater, the healthcheck, e2e and CI.
- **`is_blocked` re-checks `is_blockable`.** A network block is a CIDR and a wide one can contain loopback; checking only where blocks are created left the serving path able to 404 the healthcheck, nginx, tusd and the updater.
- **The refusal must stay byte-identical to a real 404** - same envelope, same `code`, same headers. Anything that differs is an oracle: a scanner learns which proxies are burned and can binary-search the threshold. Hence the middleware sits INSIDE `RequestId`/`SecurityHeaders` (inheriting both on the way out) and OUTSIDE `ExceptionMiddleware`. `e2e/tests/edge-behaviour.spec.ts` asserts bait probes return 404 with a JSON content-type.
- **The nginx probe limiter must carry `limit_req_status 404` and shed via `@probe_shed`.** Without it nginx sheds with its DEFAULT 503 + HTML error page - the same oracle one layer out, announced to exactly the fastest scanners. `proxy_intercept_errors` stays off, so the `error_page 404` catches only the nginx-generated 404 and never rewrites a real one proxied from the backend. **`edge-behaviour.spec.ts` cannot catch this** - three sequential requests never trip 5r/s. Shedding still hides the request from the app (no `error_log` row, `note_offence` never counts it) - a deliberate trade of detection for flood protection.
- **Detection lives in `middleware/scan_guard.py`, NOT in `middleware/errors.py`.** That hook is gated on `error_log.capture_4xx` (off by default, empty allowlist) so a guard there does nothing on a stock install, and it is throttled by an *alerting* throttle - detection would stop exactly when a scan got big. Classifying in the middleware also makes the feedback loop structurally impossible: the refusal is emitted ABOVE `ExceptionMiddleware`, so a blocked source produces **no** `error_log` rows and **no** ARQ jobs. Blocking quiets the log rather than flooding it.
- **The hot path does ZERO I/O.** Block state is a process cache, never a per-request Redis GET - `redis_client` sets `socket_timeout=2`, so a Redis *slowdown* would add two seconds to every request.
- **`/api/public/*` is never counted.** `get_link_by_token` answers 404 for an unknown token, and mail-security gateways (SafeLinks, Proofpoint, Mimecast) fetch `/d/{token}` from many egress IPs and retry - so a revoked share link looks exactly like distributed token guessing from a customer's mail infrastructure.
- **Authenticated requests never count** (no observed offence ever carried a session), which is also what stops the self-update poll's `JOB_NOT_FOUND` 404s banning the admin who clicks Update. **A 404 on a path with NO route runs no dependency**, so `user_id` is never set and an authenticated user cannot be exempted there - bounded (`api_404` ships off and needs 15 distinct paths) and pinned by its own test.
- **Paths are counted `_redact_path`'d**, so a live public-link token never lands in a Redis key or an admin-browsable table. `utils/geohash.ip_geohash5` is a ONE-WAY hash and cannot be reversed to a CIDR - use `ipaddress` for networks.

### What a Redis outage does

`check_ip_allowed` catches its own Redis errors and falls back to an in-process
counter, so `probe_path` and `auth_failure` keep counting AND blocking, per
worker, at the same thresholds. Only `api_404` truly fails open
(`_distinct_paths_seen` returns None and the caller declines). The DB-backed
block cache does fail open. "Redis down ⇒ fail OPEN" is false: the test that once
said so stubbed `check_ip_allowed` itself to raise, a call path that cannot
occur, so it pinned the docstring rather than the behaviour.

### The auth-failure signal

**`scan_guard.signal_auth_failure` ships OFF**, and could not safely be switched
on before v2.13.0.

- **A 401/403 counts only when the envelope `code` says a SUBMITTED SECRET WAS WRONG** (`_COUNTABLE_AUTH_CODES`, an **ALLOWLIST**). The middleware sees only the status, so `app_error_handler` stamps `request.state.error_code` onto the ASGI scope - the same channel the `authenticated` short-circuit uses, leaving the response bytes untouched. `TOTP_REQUIRED` is a **401 on `/api/auth/login`** and the normal first step for every 2FA user; `ACCOUNT_DISABLED` and `EMAIL_NOT_VERIFIED` are 403s raised AFTER the password verified - counting them would 404 an office off the whole product after four ordinary logins, escalating for a week. **An absent or unknown code does NOT count**: a new failure code on a credential route must opt in rather than silently start banning people.
- **`_CREDENTIAL_PREFIXES` must be real, 401-producing mounts**, pinned structurally against the router table (four of the original six were inert). **Never add `/api/auth/oidc/`** (callback failures are 302s, and `OIDC_NO_ACCOUNT` is what a legitimate SSO user without a local account gets) and **never use `/api/auth/` as a blanket** (it sweeps in `/refresh`, which 401s once per expired tab - exact prefixes are the only reason the SPA's refresh storm is not counted).
- **Credential failures count in their OWN bucket at their OWN threshold** (`scanguard_auth` / `scan_guard.auth_threshold`, default 15, floor 5). Pooling is wrong both ways: at the scan threshold of 3, two bait probes plus one password typo blocks an office; at 15, bait detection is gutted. **`check_ip_allowed` allows while `count <= limit`**, so a source lands ON the limit and is served; "fixing" that to `<` shifts everyone one attempt earlier. What actually brakes a source is the per-IP login limiter (429s are uncountable), capping ANY source at ~10 countable failures per 15 min - so 15 means roughly half an hour of doing nothing but failing.
- **The shared-egress discriminator counts failures as the four countable outcomes**, never `outcome != success`: `rate_limited`, `locked` and `account_disabled` rows are produced in volume by the very office being protected, and counting them raises the bar the successes must clear and withholds the exemption. Successes must span **≥2 distinct accounts**, or one attacker-owned login launders unlimited grinding from the same address. Not tunable: a knob to disable it is a knob to ban an office. See accepted residual **#2** (`docs/engineering/decisions.md`).
- **`login_attempts.ip` must be written in the SAME canonical form the guard counts in.** `_request_ip` goes through `get_client_ip`, so the mapped-IPv6 unwrap applies on both sides of the shared-egress join; raw, the join found zero rows on a dual-stack deployment and the office was blocked by the check meant to exempt it.
- **A release must clear the counters** (`clear_counters`). Otherwise the source is still at threshold for the rest of the window and the next request re-blocks it.

### Grouping and escalation

- **IPv4-mapped IPv6 is unwrapped at the door** (`utils/client_ip.normalize_ip`, repeated defensively in `network_of`). `is_global` was already safe, but the GROUPING was not: `::ffff:8.8.8.8` is version 6, so `network_of` yielded `::/64` - one prefix covering the whole mapped IPv4 space, unrescuable by a v4 allowlist entry (`_network_contains_allowlisted` only compares same-version networks).
- **IPv6 grouping is a setting, and /48 is deliberately unreachable.** At /64 escalation is inert for IPv6 (a routed /48 holds 65,536 /64s) - but widening is NOT the fix: the one /48 that grouped on the reference instance was a VPS pool (netcup) with one /64 PER CUSTOMER; Hetzner and Vultr allocate the same way and OVH/Linode share a /64 between customers. **Prefix length is not a proxy for tenancy.** Floor is /56, clamped in `network_of` itself as well as the registry, because `_defaults()` and `config_backup` both reach it unclamped. IPv4 stays hardcoded /24.
- **/24 escalation ships OFF** - escalating the two hot networks on the reference instance would block 512 addresses to suppress 14.
- **Escalation evidence must be FRESH.** `network_lookback_hours` (168h) is far longer than a network block (60 min), so counting over the whole window let ONE new address resurrect a lapsed network block, hourly, for a week. Count since the last network block on that prefix ended.
- **`ip_blocks.network` is a denormalised cache** of `network_of()`, compared by string equality - so changing the prefix must release live network blocks, or evidence stops matching AND an orphaned overlapping block survives the release of the visible one.
- **Never call `_ensure_fresh()` from inside an open transaction** - it opens its own `SessionLocal`, and the escalation path did, which under the test harness's StaticPool rolled back the caller's pending block. Pass the snapshot instead.

### State: allowlist, watchlist, manual blocks

- **`scan_guard.allowlist` has ONE writer**: the allowlist endpoints, which serialise on a row lock over the setting's own row (a no-op on SQLite; the first-insert race is closed by the unique key → 409 `CONFLICT_RETRY`). It was also a textarea on the settings form, i.e. a second writer carrying a stale whole-CSV snapshot that erased entries added elsewhere. The field is gone from the PUT body and from `update_settings`' `strs` map; because `APIBaseModel` allows extras, an older SPA still sending it is ignored rather than 422'd. **Do NOT make it `str | None`**: the strs loop turns an empty value into `set_value(value=None)`, which DELETES the row.
- **The watchlist holds PLAINTEXT addresses of sources that are not blocked.** The enforcement counters cannot back it (both key on `sha256(ip)[:16]`), and `error_log` cannot either (`capture_4xx` ships off, so it would render empty exactly where the guard ships). Three fixed Redis keys, never SCAN, capped at 512, quietest evicted first. **Retention is bounded by per-member pruning against the `seen` ZSET, not by EXPIRE** - EXPIRE is whole-key and any other source's write slides it, so one busy scanner would keep every address alive indefinitely. `scan_guard.watchlist` (default on) turns it off.
- **A manual block never folds into a live automatic row** - it kept `source=auto`, dropped the note and actor, wrote no audit row and could not SHORTEN the block. It releases the auto row and inserts; the auto path never mutates a manual row.
- **`scan_guard.*` tunables are NOT on `/admin/settings/advanced`** (`_MANAGED_ELSEWHERE_GROUPS`). That route wrote them while skipping the inert check, the v6-prefix live-network-block release and the cache reset - so changing the prefix there stranded an orphaned network block enforcing invisibly. `config_backup` import is a third raw writer; still a residual, and `_reconcile_after_import` is what covers it.
- **`backend/scripts/unblock_ip.py` matches by CONTAINMENT**, via the shared `blocks_covering`. A string compare meant an admin locked out by a /24 who typed their own address was told "no live block" - at the exact moment the tool exists for.
- **`note_offence` does sync Redis on the event loop, and that is a KNOWN, deliberate non-fix.** It is the whole application's pattern (every per-IP limiter is called that way from an `async def`). Moving only the guard off-loop was tried and reverted: `asyncio.to_thread` puts the guard's own `SessionLocal` on a second thread, fine against MariaDB and corrupt against the test harness's single shared SQLite connection (measured `sqlite3.InterfaceError`). Fix it for the whole app or not at all.
- **`ip_blocks.hit_count` is counted in-process and flushed on the cache refresh, never per request.** The only increment used to be in `_block()`'s extend branch, which the middleware never reaches (it returns straight out of `is_blocked`), so every row read exactly 1 and an operator could not tell a quiet block from one under sustained attack. `note_block_hit` (one dict bump, ZERO I/O - the hot path forbids it) is called from the middleware's refusal branch, NOT from `is_blocked`, because `note_offence` also calls `is_blocked` and that is not a served refusal. `_flush_block_hits` runs from `_refresh_cache` **outside its try/except**, so a failed counter write cannot reach the fail-open handler and un-block everyone. A network block is counted against its CIDR string, which is how the row is keyed.
- `ip_blocks` uses `Integer`, not `BigInteger` - `scan_guard.max_new_blocks_per_min` caps the insert rate, and BigInteger is reserved for the genuinely high-volume logs.
