# E2E coverage

Every user-facing feature of file:Heron mapped to the Playwright test that exercises it, or to the
reason it has none. Two parts, split along the router:

| Part | Scope | Specs | Map |
|---|---|---|---|
| a | Everything outside `/admin`: sign-in, registration, password reset, two-factor, passkeys, SSO, the account page, shares (compose, inbox/outbox, share page, approval), uploads, public links, secrets and secret requests, notifications, legal and 404 pages | `tests/user/`, plus the journeys in `tests/*.spec.ts` | [`COVERAGE.a.md`](COVERAGE.a.md) |
| b | The admin console (`/admin/**`) | `tests/admin/` | [`COVERAGE.b.md`](COVERAGE.b.md) |

Run everything: `scripts/e2e.sh` (CONTRIBUTING §End-to-end). The docs tour (`docs/`, run last by the
same script) is a render check of every screen in the gallery, not counted here.

Status marks: ✅ a named test exercises it; ❌ with one reason - (a) needs hardware or an external
account and no simulator exists, (b) irreversible external effect with no sink, (c) covered at another
layer (the test is cited), (d) cannot be driven, (e) time-box overrun.
