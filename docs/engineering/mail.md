# Email: notifications, templates, mail log

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `backend/app/services/{notification,notification_prefs,email,email_placeholders,mail_log,mail_test_gate,unsubscribe_token,sse,sse_token}.py`, `backend/app/utils/emailing.py`, `backend/app/templates/email/`, `backend/app/routers/{notifications,notification_subscriptions}.py`, `backend/app/routers/admin/{mail,email_templates}.py`, `backend/app/workers/send_email.py`, `frontend/src/components/NotificationBell.vue`, `frontend/src/composables/useSSE.ts`

## Email: notifications, templates, mail log

**Single funnel `services/notification.py::dispatch(db, user, category, payload,
*, email_to=None)`** - **every** callsite goes through it (no direct
`notifications` writes, no direct `send_email_job`): resolves channel (pref row →
`_DEFAULT_CHANNEL`), writes a row unless `off`, renders the locale template +
enqueues `send_email_job` when the channel includes email and `email_to` is
given. Failures are logged, never propagated. Categories + defaults:
`models/notification.py::NotificationCategory` + `_DEFAULT_CHANNEL`. Fan-outs use
`job_queue.enqueue_many` (one pool); `dispatch` accumulates on `db.info` and pairs
its `run_after_commit` flush with a `run_after_rollback` clear - without the
latter a rolled-back batch is silently adopted by the next dispatch on that
session.

- **Every mail's headers are set in `utils/emailing.py::build_message`, and From goes through `formataddr`, never an f-string.** The product's own name has a colon, and an unquoted `file:Heron <x@y>` is RFC 5322 GROUP syntax: every mail shipped as `From: file:Heron <…>;`, with no Date and no Message-ID, until v2.23.1 - Gmail rejects that, rspamd scores it, DMARC cannot take a domain from it. `build_message` also sets `Auto-Submitted: auto-generated` + `X-Auto-Response-Suppress: All` on every mail and strips the `MIME-Version` that `add_alternative` stamps on sub-parts. `test_email_headers.py` asserts on the RE-PARSED bytes; the object's own header view looks plausible either way.
- **`render_email` empties whitespace-only lines in the HTML.** Every template's layout left some (indented block tags, the optional footer lines), and quoted-printable spells each as a visible `=20` line, which reads as table rows that rendered empty. Pinned for every slug and locale in `test_email_template_matrix.py`.
- **`_resolve_subject` renders a key the payload lacks as EMPTY, never the raw template.** It returned the literal `{braces}` on a KeyError; it now trims what a missing field leaves (a trailing `: `, an empty `()`). `ops_alert`'s subject carries `{reason}`, which all eight senders pass.

### Unsubscribe: THREE tiers, not two

- `LOCKED_CATEGORIES` (`reset_password`, `login_alert`) = cannot be disabled at all; `effective_channel` ignores any stored row and forces the default.
- `NO_ONE_CLICK_CATEGORIES` (`ops_alert`, `server_error`) = **switchable deliberately on the preferences page, never by one tap from an email.** These are the instance reporting that it is broken, and as ordinary opt-out categories every alert shipped `List-Unsubscribe` + `List-Unsubscribe-Post`, so one tap on the mail client's Unsubscribe button ended alerting permanently and silently - on an instance where ONE admin may be the only recipient. **Do not "simplify" this into `LOCKED_CATEGORIES`**: locked also means read-only + forced-to-default, which would DOWNGRADE an admin who deliberately chose `both`.
- Everything else = fully opt-out-able.
- **The guard that actually holds is in `notification_prefs.unsubscribe_category`**, the chokepoint both `/one-click` (RFC 8058) and `/unsubscribe` (the SPA's `?off=`) call - NOT the footer emission, because mail already delivered still carries a live `?off=` and one-click URL. `/one-click` stays **200** on refusal (a 4xx just shows the recipient a mail-client error) but its BODY says what happened.
- **`List-Unsubscribe-Post` must read exactly `List-Unsubscribe=One-Click`** (`utils/emailing.py::ONE_CLICK_POST_VALUE`, RFC 8058 §3.1, matched literally by clients); it read `List=One-Click` for as long as the header existed, degrading one-click to the mailto fallback. `build_message` is split out of `send_email` purely so a test can assert the header on the built object.
- **`PreferenceItem.from_row` is the ONE serialiser** - the token-authed manage route and the signed-in account route each hand-built the payload, so a flag added to one was absent from the other, and both flags decide whether an alert can be switched off.
- **The unsubscribe token is a second bearer credential** - it reads a user's display name and whole preference matrix and mutates it - so it carries an issue time, `<uid>.<iat>.<exp>.<sig>`, and is invalidated by a password change, a reset, sign-out-all and an admin revoke-all. **The revocation check is in `routers/notification_subscriptions.py::_resolve`, NOT in `unsubscribe_token.verify_full`** (signature + expiry only), so probing the latter alone reports a revoked token as valid. **TTL is 30 days** (was 180): the token travels in a URL PATH, so every hop logs it verbatim - a live one was found in the reference host's world-readable Traefik access log. **It is carried by TWO routes** - the SPA's `/manage-notifications/<t>` and the API's `/api/notification-subscriptions/<t>` - so anything that redacts or matches it must key on the token SHAPE, never on one route. **Legacy three-part tokens are still accepted and must report `iat=None`, never 0**: they are in mail already delivered, and the epoch would make every one look older than any revocation mark and lock the whole population out.
- **The footer token is redacted at rest and re-minted on the way out** (`mail_log._FOOTER_LINK_RE` / `remint_footer`). Anything that re-sends a STORED body must call `remint_footer`, or it ships `/manage-notifications/<redacted>` as a working link. Only `routers/admin/mail.py`'s resend does this today; `dispatch` never reads the body back.

### In-app bell + SSE

- `services/sse.py` Redis pubsub per-user channel `fh:sse:{user_id}`; the dispatcher publishes when the channel is `in_app`/`both`. Bell in `NotificationBell.vue`.
- **A fan-out's live pushes are batched per commit like its emails** (`notification._batch_after_commit` → `sse.publish_many`: one connection, one pipeline). They were one `publish_sync` - a client and, from a sync route, an `asyncio.run` - per recipient. The rollback discard and pop-on-flush are shared with the email batch; `test_sse_batching.py` pins both.
- **Connection lifetime is 60s BY DESIGN** (deterministic reconnect beats proxy timeouts) - the server emits `: close` on TTL and the frontend reconnects with `Last-Event-Id`. EventSource auth via `?token=` (signed, 300s TTL - longer than the stream because browsers defer a background-tab connect long past the mint).
- **A route that authenticates via a signed `?token=` cannot live behind `_gate`.** The gate calls `get_actor`, which requires an `Authorization` header - exactly what EventSource cannot send. `admin.stream_router` and `notifications.stream_router` are mounted ungated in `main.py` and must stay so; folding either back re-breaks it as a 401 that looks like any other. The evidence is the CODE: `AUTH_REQUIRED` = the gate refused it, `INVALID_SSE_TOKEN` = the handler ran. The admin live-status route had never once connected because of this.
- **The stream token honours `sessions_invalidated_at`** via `jwt_session.was_issued_before_revocation` - a revoked session otherwise kept reading `/api/notifications/stream` for the token's remaining life.
- **Reverse-proxy:** `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`, `Connection: keep-alive`. **Don't add buffering middleware in Traefik labels.**
- `_catchup_frames` genuinely never raises now - `SessionLocal()` must stay inside its try; outside, the one failure its docstring promised to absorb was the one that killed a live stream.

### Mail log

Every outbound email → `email_log` via funnel `services/mail_log.py`; admin at
`/admin/mail-log`.

- **`via`:** *queued* (notifications - `dispatch` renders once, worker `workers/send_email.py` finalizes the row by id), *direct* (auth-flow), *test* / *dev_fallback* (SMTP unconfigured), *resend*. `send_email_job` declines a redelivery whose row already says `sent`.
- **Masking (fail-closed):** `mask_sensitive` redacts tokens in reset/verify/register URLs; forced for auth-link categories; any regex error → placeholder (never persist a live token). `masked` (or via test/dev_fallback) **disables resend**.
- **Retention** `retention.email_log_days` (90, 0 disables). **Erasure** scrubs the target's rows in place (PII gone, flow counts kept).

### Email templates

Overrides: `models/email_template_override.py` `UNIQUE(slug, locale)`;
`services/email.py::render_email` consults the table first and falls back to the
built-in filesystem Jinja template - **"Reset to default" just deletes the row**.
Body is **HTML** (`body_html`); legacy `body_markdown` stays NOT NULL written
`""`. Stored **raw** (token hrefs like `[RESET_URL]` must survive the editor),
**sanitised at render**, then alignment classes inlined for mail clients
(`email.py::_inline_alignment`). NULL subject = inherit from `subjects.json`;
`_load_override` falls back to `en`. Placeholder registry:
`services/email_placeholders.py`.

- **Every slug ships FOUR files**: `{en,de}` × `{txt,html}`. `tests/test_email_template_matrix.py` is the ratchet - it takes the slug list from `subjects.json`, **never a hand-written list**, requires all four files BY FULL PATH, renders every combination, and asserts en and de render DIFFERENT html - what catches a missing `de/` file hiding behind the en fallback.
- **`server_error` is SHARED with `scripts/send_ops_alert.py`**, which passes `source="ops"` and no method, path, status or exception type - without an `ops` branch the template rendered four literal `None`s on the mail that tells an operator their backups have stopped. The script must spell the count `occurrence_count`, the key the template reads. Both pinned by `tests/test_ops_alert_rendering.py`, with a control that the HTTP path still renders its request line.
- **`_render`'s locale fallback must stay `except TemplateNotFound`** - a bare `Exception` let a SYNTAX ERROR in a `de/` template fall through to `en/`, so the recipient got a German text part beside an English HTML part, silently.
- **The layout DEFINES `{% block subject %}` with a default.** Only calling `{{ self.subject() }}` made the block mandatory in every child, which left `release_available.html.j2` dead in both locales for its whole life (`UndefinedError` into `render_email`'s bare `except`, nothing logged). Do not remove the default.
- **Guard the German footer date on TRUTHINESS, not `is defined`** - `_wrap_layout` passes `now=ctx.get("now")` unconditionally, so on the admin-override path the name is defined and None, which is how every German email shipped a dangling `Empfangsdatum: .`
- **`_components.html.j2` must keep its `.html.j2` suffix** - that is what puts it inside `select_autoescape(enabled_extensions=("html.j2",))`; renaming it silently turns escaping OFF inside every macro body. It is imported WITHOUT context on purpose: `dt_locale` is a `@pass_context` filter, so a macro calling it would read an empty context and format every timestamp in UTC - format at the call site. Every content macro accepts both a plain argument and a `{% call %}` body; a `caller()`-only macro raises "No caller defined", which `render_email` turns into a silently text-only email.
- **An auth token must stay in the canonical `href="…/reset-password/<token>"` path form.** `mail_log.mask_bodies` masks BOTH bodies, but `_AUTH_LINK_RE` only matches that shape, and a non-match returns the body VERBATIM while `masked=True` still claims it was handled. Wrapping, URL-encoding or line-breaking the link defeats masking silently. Pinned per slug, with a negative control.
- **Admin overrides stay plain by construction** - `richtext.sanitize_html` strips every `style` attribute, so an override can never carry the built-in styling; `_default_body` seeds the editor from the `.txt.j2` deliberately. Don't "fix" it by widening the sanitiser.

### The mail test-connection gate

**`services/mail_test_gate.py`'s condition is an INTERSECTION**: the stored
SMTP/IMAP secret may only travel to the SAVED server unless the caller
re-authenticates. Testing the saved server, or a new one with a freshly typed
password, prompts for nothing - gating on host mismatch alone would break "try a
new provider before saving it", the entire reason the override exists. **Compare
resolved values, never the raw payload**: the SPA sends `user: ''` for "use SMTP
credentials" and omits `port` while the input is empty, both meaning "keep the
stored one", so a raw comparison prompts on every click and the fix gets reverted
as unusable. Without this the stored mail password went in cleartext to any host
the caller named - measured, not theorised. **`utils/net.py::assert_safe_host`
never mitigated this and cannot**: it is an ADDRESS policy with
`allow_private=True` that **fails open on an unresolvable name**, unlike
`assert_public_http_url` - deliberately, because these endpoints exist to report
connection errors legibly and a host that does not resolve cannot be a target.
