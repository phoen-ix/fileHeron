# Phase 2 — Frontend Auth UI

> Master plan: `REDACTED/.claude/plans/i-want-to-create-melodic-whale.md` — see the **Design system** section there for the locked aesthetic direction.
> Depends on Phase 1a + 1b being complete.

## Goal

Build a Vue 3 + Vite + Pinia + Vue Router + axios SPA with all authentication-related screens — but with a **custom design system**, not a port of any other project. The design system is editorial Swiss-modernist with a denser "operator mode" on power-user surfaces. Element Plus is used **selectively** for utilitarian primitives (date pickers, dialogs, dropdowns) themed via CSS variables to match the design system; it is NOT used as the dominant visual language.

Pages: Login (with TOTP challenge + recovery toggle), RegisterFromInvite, ForgotPassword, ResetPassword, EmailVerify, TwoFactorSetup, Account (profile, change password, 2FA management, active sessions list with revoke). Replace the static placeholder served by `nginx-spa` with the real Vue build.

## Locked design decisions (per master plan)

- **Typography (free, self-hosted via @fontsource):** Instrument Serif (display) + Geist Variable (body) + Geist Mono Variable (data).
- **Color (light theme):** ink `#1a1d24`, paper `#faf8f3`, accent `#b45309`, subtle `#6b7280`, hairline `#e5e1d8`, success `#4d7c0f`, danger `#991b1b`. No dark theme this phase.
- **Density modes:** editorial (client-facing, generous whitespace) and operator (sender/admin, denser grid + mono data + keyboard hints). Same tokens, different rhythm.
- **Spacing:** 4 / 8 / 16 / 24 / 40 / 64 / 96.
- **No** stock Element Plus theme. **No** Tailwind. **No** purple gradients. **No** AI-cliché aesthetics.

## Pre-phase decisions

1. **Initial language** — *Default: EN; user can switch in account settings (saved on `User.locale`).*
2. **Password strength meter** — *Default: simple length+class for Phase 2; switch to zxcvbn-ts in Phase 7 alongside HIBP.*
3. **CSS approach** — vanilla CSS modules + design tokens (CSS variables in `:root`), NOT Tailwind/UnoCSS. Keeps the bundle small and the design intentional.

## Acceptance criteria

- `nginx-spa` now serves the Vue build (multi-stage Docker build: vite → nginx:alpine).
- All these flows work in a real browser end-to-end against a Phase 1b backend:
  - Click an invite link → `/register/{token}` page → set password + display name + locale → auto-login → land on `/`.
  - Login with email + password (no 2FA) → land on `/`. Login with 2FA → see code prompt → submit → land on `/`. Recovery code link works.
  - "Forgot password" → email logged → click link → `/reset-password/{token}` → set new password → invalidates all sessions → login again.
  - "Verify email" link from welcome email → `/verify-email/{token}` → success message → return to login.
  - 2FA setup wizard: shows QR (server-rendered SVG inline), shows 10 recovery codes once with "I've saved them" gate, enables TOTP. Disable flow requires password + code.
  - Account page: edit display name, change password, list active sessions (with current-session badge), revoke individual sessions.
- Axios interceptor: on 401 → call `/api/auth/refresh` → retry once. On refresh fail → clear store → redirect to `/login?redirect=$path`.
- `vitest run` green on auth store, axios interceptor, RecipientPicker stub.

## Files to create

### Frontend — config & shell
- `frontend/package.json` — deps + scripts. See dep list below.
- `frontend/vite.config.ts` — vue plugin, path alias `@` → `src/`, dev proxy `/api → backend:8000`.
- `frontend/tsconfig.json`, `frontend/tsconfig.app.json`, `frontend/tsconfig.node.json` — match `REDACTED/frontend/`.
- `frontend/index.html`

- `frontend/src/main.ts` — Vue app, Pinia, Router, vue-i18n, Element Plus locale registration.
- `frontend/src/App.vue` — top-level layout with router-view.

### Frontend — API client
- `frontend/src/api/client.ts` — axios instance, base URL = `'/api'`, interceptors for auth + refresh-rotation. Port from `REDACTED/frontend/src/api/client.ts` and `frontend/src/stores/auth.ts`.
- `frontend/src/api/auth.ts` — typed wrappers for register, login, refresh, logout, forgot/reset, verify-email, login-recovery.
- `frontend/src/api/account.ts` — me, change-password, sessions.
- `frontend/src/api/twoFactor.ts` — setup, enable, disable, regenerate-recovery.

### Frontend — stores
- `frontend/src/stores/auth.ts` — Pinia store: `user`, `accessToken` (in-memory only), `isAuthenticated`, actions for `login`, `logout`, `refreshSilently`, `loadMe`, `setLocale`.
- `frontend/src/stores/ui.ts` — global UI state (loading, error toast queue).

### Frontend — router
- `frontend/src/router/index.ts` — public routes (`/login`, `/register/:token`, `/forgot-password`, `/reset-password/:token`, `/verify-email/:token`) and authed routes (`/`, `/account`, `/account/2fa`, `/account/sessions`). `beforeEach` guard: try silent refresh once; if not authed, redirect to `/login?redirect=...`.

### Frontend — views
- `frontend/src/views/Layout.vue` — header (with language switcher + user menu), `<router-view/>`.
- `frontend/src/views/Login.vue` — email/password form, TOTP-code form (revealed on `TOTP_REQUIRED`), "use recovery code" toggle.
- `frontend/src/views/RegisterFromInvite.vue` — accepts token from URL, shows email-hint, sets password + display name + locale.
- `frontend/src/views/ForgotPassword.vue` — email field, success message regardless of outcome.
- `frontend/src/views/ResetPassword.vue` — accepts token, new-password form.
- `frontend/src/views/EmailVerify.vue` — automatic verify-on-mount with success/failure result.
- `frontend/src/views/Account.vue` — profile (display name, locale), change-password section, 2FA section (link to wizard), sessions list with revoke.
- `frontend/src/views/TwoFactorSetup.vue` — wizard: scan QR (inline SVG from API) → enter code → see recovery codes + "I've saved them" checkbox → done.
- `frontend/src/views/HomePlaceholder.vue` — "Welcome — file features land in Phase 3."

### Frontend — components
- `frontend/src/components/AppHeader.vue` — logo, user dropdown, language switcher, logout.
- `frontend/src/components/LanguageSwitcher.vue` — DE/EN toggle, calls `auth.setLocale`.
- `frontend/src/components/PasswordStrength.vue` — simple length+class indicator.
- `frontend/src/components/SessionRow.vue` — single session row with current badge + revoke button.

### Frontend — i18n
- `frontend/src/i18n/index.ts` — vue-i18n setup; default from `User.locale` or browser; fallback EN.
- `frontend/src/i18n/locales/en.json` — all auth-flow strings.
- `frontend/src/i18n/locales/de.json` — same in German.

### Frontend — styles
- `frontend/src/styles/global.scss` — base resets + Element Plus tweaks.

### Frontend — tests
- `frontend/tests/api.client.test.ts` — refresh interceptor logic.
- `frontend/tests/stores/auth.test.ts` — login flow, silent refresh, logout cleanup.

### Docker
- `docker/frontend/Dockerfile` — multi-stage: `node:22-alpine` for `npm ci && vite build`, then `nginx:alpine` serving `/usr/share/nginx/html` + tiny `/healthz`.
- `docker/frontend/Dockerfile.dev` — `node:22-alpine` running `vite` with HMR.
- `docker/frontend/nginx.conf` — try-files SPA fallback to `index.html`, security headers (basic; full headers live on FastAPI), `/healthz` returns 200.

### Compose
- `docker-compose.yml` — replace placeholder `nginx-spa` with the new built image.
- `docker-compose.dev.yml` — add `frontend-dev` service running Vite with HMR, mount source.

### Root docs
- `CLAUDE.md` — extend Project structure with `frontend/` tree; document Vue 3 conventions used (Composition API + `<script setup>`).

## DB migrations

None this phase.

## API endpoints

None new. UI consumes Phase 1a/1b endpoints.

## Frontend routes

| Path | View | Public/Authed |
|---|---|---|
| `/login` | Login.vue | public |
| `/register/:token` | RegisterFromInvite.vue | public |
| `/forgot-password` | ForgotPassword.vue | public |
| `/reset-password/:token` | ResetPassword.vue | public |
| `/verify-email/:token` | EmailVerify.vue | public |
| `/` | HomePlaceholder.vue | authed |
| `/account` | Account.vue | authed |
| `/account/2fa` | TwoFactorSetup.vue | authed |

## Dependencies added

**npm:**
- runtime: `vue@^3.5`, `vue-router@^4`, `pinia@^2`, `element-plus@^2.9`, `axios`, `dayjs`, `vue-i18n@^11`, `@vueuse/core`
- dev: `vite@^6`, `@vitejs/plugin-vue`, `vitest@^4`, `@vue/test-utils`, `happy-dom`, `vue-tsc`, `typescript@~5.6`, `eslint`, `eslint-plugin-vue`, `prettier`, `sass`

**pip:** none.

## Risks / pitfalls

1. **CSP and Element Plus inline styles** — Element Plus injects scoped styles inline; needs `style-src 'self' 'unsafe-inline'`. Document the relaxation in CLAUDE.md and the security-headers middleware (Phase 1b ships the relaxed CSP already).
2. **Server-rendered QR SVG** — render QR on backend (`qrcode[pil]`) and inline into HTML. Avoids shipping a JS QR lib and keeps the secret off the frontend bundle.
3. **One-time recovery codes** — must force user confirmation ("I've saved these") before navigating away; persist a one-shot flag in Pinia so revisiting `/account/2fa` doesn't show stale codes.
4. **Refresh interceptor concurrency** — multiple simultaneous 401s should share a single `/api/auth/refresh` call. Implement promise-coalescing in `client.ts`.
5. **HMR + cookies** — Vite HMR sometimes loses cookies on reload; document workaround (`SameSite=Lax + dev proxy`).
6. **Element Plus locale** — must register both `de` and `en` locales explicitly; default depends on user.

## Verification

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
# open http://127.0.0.1:8081 (or whatever frontend port)
# manually run through every flow listed in acceptance criteria

cd frontend && npm run test
```

## Out of scope

- File upload UI → **Phase 3b**
- Group / share UIs → **Phase 4**, **Phase 6b**
- Notifications bell → **Phase 6b**
- OIDC sign-in button → **Phase 7**
