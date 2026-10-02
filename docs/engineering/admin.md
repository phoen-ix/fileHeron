# Admin console and the settings store

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `frontend/src/config/admin*.ts`, `frontend/src/views/Admin*.vue`, `frontend/src/components/admin/`, `frontend/src/components/RichTextEditor.vue`, `backend/app/services/{account_prefs,erasure,user_management,invite,settings,settings_registry,policy_gate,site,richtext}.py`, `backend/app/routers/admin/settings/`, `backend/app/routers/{branding,users}.py`

## Admin

→ README §Admin guide for pages and endpoints. `/admin` = `AdminLayout.vue`
(sidebar + nested routes), `requireAdmin` meta + `get_current_admin` dependency.

- **The sidebar is `config/adminNav.ts`, six task-based categories + an Overview** (`people · sharing · email · security · site · system`; keys mirrored by `services/account_prefs.ADMIN_NAV_CATEGORIES_ORDER` and pinned by `tests/test_admin_nav_categories_pin.py`, which reads both files). No category may exceed seven items, and a new page goes in the category of its TASK - the previous four categories grew one appended entry per release until System held 14 of 32. `sharing` is AT the cap since Secrets (v2.24.0). **A policy and the state it produces are TABS on one item - and so are the two halves of a page that outgrew one form (Branding | Legal pages)** (`AdminNavItem.tabs`, rendered by `views/AdminTabShell.vue` + `components/admin/AdminTabs.vue`): the router mounts the shell at the item's path with the tab leaves as children, the second tab's historical path as an ABSOLUTE child path (`/admin/settings/scan-guard` under `ip-blocks`) so no URL, route name, email link or persisted `notifications.link_url` changed. **Every tab leaf must be in the item's `matchNames`** - `route.name` is always the LEAF, so a missing one gives a page whose sidebar highlights nothing; `adminNav.test.ts` pins router names ⊆ `ADMIN_ROUTE_NAMES`. Persisted `admin_nav_open_categories` holding old keys need no migration (`seed()` drops unknown keys; GET never re-validates). `/admin` is `AdminOverview.vue`: attention tiles, the setting search over `config/adminSearchIndex.ts` (a STATIC registry - codegen was rejected for the same reasons as the types mirror - pinned by `adminSearchIndex.test.ts` and `test_admin_search_index_pin.py`), and category cards rendered from `ADMIN_NAV`. **The search box is not `input[type=search]`** and its placeholder avoids the word "search": `useKeyboardShortcuts`' `/` focuses the first such input in DOM order.
- **Every admin view's heading is `components/admin/AdminPageHeader.vue`**: clickable crumb (Admin › category › page) + the `<h1>`, both resolved from the SAME `admin.nav.*` key the sidebar uses, so a nav label and its page title cannot drift (they drifted on six pages while 37 views hand-built the heading in five flavours). `page_title.admin_*` carries the same string for the browser tab. Detail pages pass `:title`/`#title` + `:back-to`; `hide-title` keeps a view's own `<h1>`, and `tests/test_frontend_a11y_tokens.py` accepts the component as the heading only without it. Router-less view tests stub it with a slot-rendering stub, not `true` - controls moved into `#actions` vanish otherwise. **A tab leaf renders NO header of its own** - `AdminTabShell` owns it; two leaves did after v2.17 (two `<h1>`), pinned over every tab leaf by `test_no_tab_panel_renders_its_own_page_header`.
- **App.vue keys admin routes on the LAYOUT, not the path** (`utils/viewKeys.ts`): keyed on `route.path`, every admin click remounted AdminLayout and a tab switch replaced the tab strip mid-keypress. AdminLayout keys its child on record + params, so a detail page still remounts per id; the inbox badge refreshes around the inbox pages since the layout no longer remounts.
- **Right-to-erasure** (`services/erasure.py::erase_user`, irreversible): hard-delete the target's files; delete TOTP/recovery/refresh/API tokens; anonymize the row (`email→erased-<id>@erased.invalid`, `display_name→[erased]`, `password_hash→""`, `is_disabled`, `oidc_subject=NULL`); audit `user_erased`. Pre-flight counts + verifiable PDF receipt (reportlab). Self-erasure refused. Erasure holds a Redis run lock, because its per-file commit releases the row lock. `prune_history` never deletes `user_erased`.
- **Self-service profile:** `PATCH /api/account/{locale,display-name,default-landing-page}`. `services/account_prefs.py` holds the ALLOWLIST (`ALLOWED_LANDING_ROUTES`) plus the admin-sidebar preference constants (`ADMIN_NAV_MODES`, `ADMIN_NAV_CATEGORIES` + `_ORDER`, mirrored by `frontend/src/config/adminNav.ts`); the resolution itself is frontend-side in `composables/useEffectiveLanding.ts`. **There is no `effective_landing_route` function** - don't grep for one.
- **Invites:** `POST /api/account/invite` pre-flights `USER_EXISTS`/`INVITE_PENDING`/`GROUP_NOT_FOUND`; `initial_group_ids` auto-applied on consume.
- `services/connection.py` lost 49 lines no caller reached, including an unguarded admin branch - don't restore them from an old diff.

### Settings store (`app_settings`)

`(key, value, is_encrypted, updated_at, updated_by_id)` generic kv overlay over
env; `services/settings.py::{get,get_bool,get_int,set_value}`. `Keys` is the
authoritative key list; `_ENCRYPTED_KEYS = {smtp.password, imap.password}`
(Fernet, same HKDF as TOTP). PATCH for secret keys: `null`=leave, `""`=clear,
other=replace. **Settings-change audits never record a SECRET value**; they record keys and counts, and
a non-secret value an investigator needs where it is the forensic fact (site URL/timezone from→to - a
changed site URL redirects every mailed link -, a scan-guard allowlist entry, a cron schedule). Every
settings change is filed `target_type="settings"` with a `target_id` naming its area, which is what the
Overview's "Recently changed" panel (`GET /api/admin/audit-log/settings-changes`) links by, through
`config/adminSettingsChanges.ts`; `test_settings_change_links_pin.py` scans every audit call for both rules.

Policy-gate pattern (mode ∈ everyone/employees_admins/admins_only + additive
user/group allowlists; admin always passes): `api_token.*`, `public_link.*`,
`share_approval.*`. The SPA renders that block once, `components/admin/PolicyGate.vue`
(labels arrive translated - the pages word it differently; the Secrets gates share its
`usePolicyAllowlist` composable), and `PolicyPages.test.ts` snapshots each page's markup. **The registry** (`services/settings_registry.py::TUNABLES`) -
each entry overlays a `config.Settings` env default, clamped, read live via
`effective(db,key)` (no boot cache). **One writer**: `PUT /api/admin/settings/advanced`.
**Many surfaces**: the frontend's `config/adminTunablePlacement.ts` says which
admin PAGE renders each group (sessions → Sessions › Policy, rate limits + HIBP →
Sign-in policies, the public-link brute-force triple → Public links,
uploads/downloads → Files & transfers, anomaly → its own page, error_alert →
Errors & alerts, updates → Status & updates, branding → Branding & legal;
retention + storage stay on `/admin/settings/advanced`, now named "Data retention
& storage"), and `components/admin/TunableFields.vue` filters the endpoint's items
by it, so a key renders on exactly one page. `null` placement = the page's own
form owns the key (the Errors page's anti-flood + retention fields). Pinned three
ways: `adminTunablePlacement.test.ts` (routes exist), `test_admin_search_index_pin.py`
(the search index points every rendered tunable at the page that renders it, and
form-owned keys carry no anchor), and the placement fallback sends an unknown
group to the Advanced page so a new tunable is never invisible. Groups in
`_MANAGED_ELSEWHERE_GROUPS` are excluded from the endpoint because that route
bypasses the side effects `update_settings` applies; the one cache the route DID
skip (`error_log.scan_capture_per_min`, ~60s) is reset by it since v2.17.

## Branding, legal pages, rich text

- `routers/branding.py`; admin `/admin/settings/branding` with the legal pages as its second tab, `/admin/settings/legal` (two halves with separate Saves that shared one form and nothing else; the legal language switch is `aria-pressed` toggles, not a second tablist); SPA `LegalPage.vue` serves `/imprint` + `/privacy`. **`/api/branding/logo` + `/api/legal/{kind}` are anonymous by design** (login page, public-link pages and emails need them). Logo served through the **storage backend** (`serve_response`, works on S3); locator in `app_settings`; `Cache-Control public max-age=24h`. `/api/branding/logo.png` is the client-sized rendition, 404s when `branding.show_client` is off. Legal HTML sanitised **on save AND on serve**.
- The admin legal-pages + email-template editor is a from-scratch **ProseMirror** (MIT) HTML editor (`components/RichTextEditor.vue` + `components/richtext/{schema,html}.ts`). Content is **HTML**, sanitised by the shared `services/richtext.py::sanitize_html` (nh3; alignment is a value-filtered `text-{left,center,right,justify}` class, no inline style). **Only true-MIT libs - never TipTap.**
