# E2E coverage, part a: the user side

Everything outside `/admin`: sign-in and the account, shares, uploads, public links, secrets,
notifications. The admin console is part b (`COVERAGE.b.md`); the index is `COVERAGE.md`.

- **Routes** (2026-10-30, 30 items): every router path outside the `/admin` block -
  ```sh
  sed -n "1,/\/\* admin ---/p;/path: '\/account\/2fa\/forced'/,\$p" frontend/src/router/index.ts | grep -oE "path: '[^']+'"
  ```
- **Features** (2026-10-30, 97 items): the forms, actions, dialogs and error states of the views and
  components those routes render (`frontend/src/views/{Login,LoginSecondFactor,RegisterFromInvite,ForgotPassword,ResetPassword,EmailVerify,ConfirmEmailChange,CancelEmailChange,Setup,Account,TwoFactorSetup,ShareList,ShareCreate,ShareDetail,Approvals,Secret*,Public*,ManageNotifications,LegalPage,NotFound}.vue`
  and `frontend/src/components/{AppHeader,ApiTokenPanel,WebAuthnPanel,OIDCConnectPanel,NotificationBell,NotificationPreferences,RecipientPicker,FileUploadArea,FileRow,FilePreviewModal,PublicLinkPanel,SecretReveal,SecretAnswerForm}.vue`),
  listed by hand from their `@submit`/`@click` handlers and error branches (`grep -nE "@submit|@click" <file>`).
- Roles: guest, client, employee, admin. There is no theme switch (light theme only - CLAUDE.md
  §Design system), so no theme row.
- Run: `scripts/e2e.sh` (whole suite + docs render check) or `scripts/e2e.sh tests/user/<spec>`; specs
  in `e2e/tests/user/` (this part) and the older journeys in `e2e/tests/`. Test ids below are
  `<file under e2e/tests/> › <title>`.

Totals: 30 routes × 4 roles = 120 sweep cells, all ✅. Features: 97 - ✅ 87, ❌ 10 (a 0, b 0, c 10, d 0, e 0).

## Routes × roles (breadth)

One parameterised sweep, `user/sweep.spec.ts › <role> › <route>`: each role visits each route, lands
where the router sends it (guest on a signed-in route → `/login?redirect=…`; signed in on `/login`,
`/setup`, `/login/2fa`, `/` → its landing page), and must show a level-1 heading and a titled tab,
finish loading, throw no `pageerror` and get no 5xx. A guest is checked on every route too.

| Route | guest | client | employee | admin |
|---|---|---|---|---|
| `/setup` | ✅ | ✅ | ✅ | ✅ |
| `/login` | ✅ | ✅ | ✅ | ✅ |
| `/login/2fa` | ✅ | ✅ | ✅ | ✅ |
| `/register/:token` | ✅ | ✅ | ✅ | ✅ |
| `/forgot-password` | ✅ | ✅ | ✅ | ✅ |
| `/reset-password/:token` | ✅ | ✅ | ✅ | ✅ |
| `/verify-email/:token` | ✅ | ✅ | ✅ | ✅ |
| `/confirm-email-change/:token` | ✅ | ✅ | ✅ | ✅ |
| `/cancel-email-change/:token` | ✅ | ✅ | ✅ | ✅ |
| `/` | ✅ | ✅ | ✅ | ✅ |
| `/account` | ✅ | ✅ | ✅ | ✅ |
| `/account/2fa` | ✅ | ✅ | ✅ | ✅ |
| `/account/2fa/forced` | ✅ | ✅ | ✅ | ✅ |
| `/outbox` | ✅ | ✅ | ✅ | ✅ |
| `/inbox` | ✅ | ✅ | ✅ | ✅ |
| `/share/new` | ✅ | ✅ | ✅ | ✅ |
| `/approvals` | ✅ | ✅ | ✅ | ✅ |
| `/share/:id` | ✅ | ✅ | ✅ | ✅ |
| `/secrets` | ✅ | ✅ | ✅ | ✅ |
| `/secrets/new` | ✅ | ✅ | ✅ | ✅ |
| `/secrets/requests/new` | ✅ | ✅ | ✅ | ✅ |
| `/secrets/requests/:id` | ✅ | ✅ | ✅ | ✅ |
| `/secrets/:id` | ✅ | ✅ | ✅ | ✅ |
| `/d/:token` | ✅ | ✅ | ✅ | ✅ |
| `/s` | ✅ | ✅ | ✅ | ✅ |
| `/r` | ✅ | ✅ | ✅ | ✅ |
| `/manage-notifications/:token` | ✅ | ✅ | ✅ | ✅ |
| `/imprint` | ✅ | ✅ | ✅ | ✅ |
| `/privacy` | ✅ | ✅ | ✅ | ✅ |
| `/:pathMatch(.*)*` (404) | ✅ | ✅ | ✅ | ✅ |

Role gate on the admin console for non-admins: `user/sweep.spec.ts › client › /admin is bounced to the landing page`, `user/sweep.spec.ts › employee › /admin is bounced to the landing page` ✅.

## Features (depth)

### Sign-in, sign-out, registration, password reset

| # | Feature / state | Roles | Test | |
|---|---|---|---|---|
| A1 | Sign in with email + password | guest | `auth.spec.ts › login with valid credentials lands in the app` | ✅ |
| A2 | Wrong password: error, stays on /login | guest | `user/auth.spec.ts › a wrong password is refused with a message` | ✅ |
| A3 | Account lock after five failures (even the right password is refused) | guest | `user/auth.spec.ts › five wrong passwords lock the account, even against the right one` | ✅ |
| A4 | Disabled account cannot sign in | guest | `user/auth.spec.ts › a disabled account cannot sign in` | ✅ |
| A5 | Deep link kept through sign-in (`?redirect=`) | guest | `user/sweep.spec.ts › a deep link survives the sign-in` | ✅ |
| A6 | Silent refresh keeps a reload signed in | client | `session-refresh.spec.ts › a fresh page boot silently refreshes from the cookie and stays authed` | ✅ |
| A7 | Sign out from the user menu | client | `user/auth.spec.ts › sign-out from the user menu ends the session` | ✅ |
| A8 | Header navigation; the phone-width user menu | employee | `user/auth.spec.ts › the header and, on a phone, the user menu reach every user page` | ✅ |
| A9 | Language switch on the signed-out pages | guest | `user/auth.spec.ts › the sign-in page switches to German and back` | ✅ |
| A10 | Site announcement (MOTD) on the sign-in page | guest | `user/auth.spec.ts › the announcement the admin sets shows on the sign-in page` | ✅ |
| A11 | Forgot password → mailed link → reset; link single-use; old password refused | guest | `user/auth.spec.ts › forgot password: the mailed link resets it once, and only the new one signs in` | ✅ |
| A12 | Forgot password: unknown address gets the same answer | guest | `user/auth.spec.ts › forgot password answers an unknown address exactly like a known one` | ✅ |
| A13 | Invite → register (name, password, language) → signed in | guest | `user/auth.spec.ts › an invite registers in German and signs the new account in` | ✅ |
| A14 | Invite link single-use | guest | `user/auth.spec.ts › an invite registers in German and signs the new account in` | ✅ |
| A15 | Invalid invite link refused | guest | `user/auth.spec.ts › an invalid invite link is refused on submit` | ✅ |
| A16 | Admin invite → register (admin API side) | admin | `invite-register.spec.ts › admin invite -> stdout token -> register lands in the app` | ✅ |
| A17 | Verify email with a valid link | guest | ❌ (c) no product flow creates an unverified account (invite, admin create, setup and SSO all verify), so the link can only be minted in the database; `backend/tests/test_token_single_use.py::test_email_verify_token_is_single_use`, `backend/tests/test_auth_replay_and_disclosure.py::test_resend_verification_actually_sends`; the invalid-link state is the sweep's `/verify-email/:token` | ❌ |
| A18 | First-admin setup wizard | guest | ❌ (c) the e2e stack boots with an admin, so `/setup` only redirects (sweep `/setup`); `backend/tests/test_setup_wizard.py::test_complete_setup_creates_admin`, `frontend/tests/views/Setup.test.ts` | ❌ |

### Two-factor, passkeys, SSO

| # | Feature / state | Roles | Test | |
|---|---|---|---|---|
| B1 | TOTP enrolment: QR + secret, wrong code refused, recovery codes shown once, acknowledgement | client | `user/auth.spec.ts › two-factor: enrol, sign in with a code and a recovery code, regenerate, turn off` | ✅ |
| B2 | Sign in with a TOTP code (wrong code refused) | client | same test; `login-totp.spec.ts › login requires + accepts a TOTP code once 2FA is enabled` | ✅ |
| B3 | Sign in with a recovery code; a used one is refused | client | `user/auth.spec.ts › two-factor: enrol, sign in with a code and a recovery code, regenerate, turn off` | ✅ |
| B4 | Regenerate recovery codes (password + code) | client | same | ✅ |
| B5 | Turn two-factor off (password + recovery code) | client | same | ✅ |
| B6 | 2FA status and codes left on the account page | client | same | ✅ |
| B7 | Policy-forced enrolment | employee | `forced-2fa.spec.ts › a 2FA-required user without TOTP is forced to /account/2fa` | ✅ |
| B8 | Passkey instead of the authenticator code | employee | `user/auth.spec.ts › a registered passkey stands in for the authenticator code` | ✅ |
| B9 | Passkey sign-in on its own (autofill and button); passkey step after a password | client | `passkey-signin.spec.ts › a passkey signs in on its own, and finishes a password sign-in` | ✅ |
| B10 | SSO button → IdP → signed in; first sign-in links the verified email | client | `user/sso.spec.ts › the first SSO sign-in links the account with that verified email` | ✅ |
| B11 | SSO identity with no account: sent back with the reason | guest | `user/sso.spec.ts › an identity with no account is sent back with the reason` | ✅ |
| B12 | Second-factor page after SSO (wrong code, right code) | employee | `user/sso.spec.ts › an account with two-factor stops at the second-factor page after SSO` | ✅ |
| B13 | Second-factor page: recovery code / passkey variants, expired pending token | employee | ❌ (c) `frontend/tests/components/LoginSecondFactor.test.ts` ("can switch to a recovery code", "offers both when the account has both", "sends the user back to login when the pending token has expired"), `backend/tests/test_second_factor_after_sso_and_passkey.py` | ❌ |
| B14 | Connect / disconnect SSO from the account page | client | `user/sso.spec.ts › SSO is connected and disconnected from the account page` | ✅ |

### Account

| # | Feature / state | Roles | Test | |
|---|---|---|---|---|
| C1 | Display name: cancel, save | employee | `user/account.spec.ts › profile: display name, language and landing page` | ✅ |
| C2 | Language EN ↔ DE | employee | same | ✅ |
| C3 | Default landing page, used at the next sign-in | employee | same | ✅ |
| C4 | Admin sidebar collapse mode | admin | `user/account.spec.ts › an admin picks how the admin sidebar collapses` | ✅ |
| C5 | Change password: wrong current refused; success keeps this tab, signs other devices out | client | `user/account.spec.ts › password: a wrong current password is refused; a change keeps only this device` | ✅ |
| C6 | Email change confirmed by the link to the new address | client | `user/account.spec.ts › email change › the link mailed to the new address confirms the change` | ✅ |
| C7 | Pending email change cancelled on the page / by the old-address link; dead confirm link | client | `user/account.spec.ts › email change › a pending change is cancelled from the account page or the old address` | ✅ |
| C8 | Sessions: revoke one, sign out all others | client | `user/account.spec.ts › sessions: revoke one, then sign out all others` | ✅ |
| C9 | API token: step-up (wrong password in the dialog), shown once, works, revoke | employee | `user/account.spec.ts › API tokens: limited scopes, created behind the password, shown once, used, revoked` | ✅ |
| C10 | API token scope enforcement | admin | `api-token-scope.spec.ts › a shares:read token is refused on a shares:create call (403)` | ✅ |
| C11 | Notification preferences per event | client | `user/account.spec.ts › notification preferences save per event` | ✅ |
| C12 | Passkey added (step-up) and removed | client | `user/account.spec.ts › passkeys: one is added behind the password and removed again` | ✅ |
| C13 | Section quick-nav | client | ❌ (c) `frontend/tests/components/SectionQuickNav.test.ts` | ❌ |

### Shares

| # | Feature / state | Roles | Test | |
|---|---|---|---|---|
| D1 | Compose: files, subject, message, user + group recipients, expiry preset, download limit, progress, View share | employee | `user/shares.spec.ts › an employee composes a share for a user and a group; the recipients download it` | ✅ |
| D2 | Recipient (direct and via group): inbox → share → download; no owner controls | client | same | ✅ |
| D3 | Send blockers (no file, no recipient, typed-not-picked) and the public-link escape | employee | `user/shares.spec.ts › Send stays disabled while the blockers list says why` | ✅ |
| D4 | Client's share to the company; employees receive it | client, employee | `user/shares.spec.ts › a client sends files to the company; employees receive them` | ✅ |
| D5 | Outbox: subject search, state filter, clear filters | employee | `user/shares.spec.ts › outbox: search, state filter, and bulk expiry` | ✅ |
| D6 | Outbox bulk expiry with its confirm dialog | employee | same | ✅ |
| D7 | Outbox: sort, group by, filter by recipient | employee | `user/shares.spec.ts › outbox: sort by subject, group by recipient, filter by recipient` | ✅ |
| D8 | List paging | employee | ❌ (c) needs more than a page of shares; `frontend/tests/components/Pager.test.ts`, `backend/tests/test_pagination_tiebreaker.py` | ❌ |
| D9 | Someone else's share is refused | client | `user/shares.spec.ts › someone else's share is refused, not shown` | ✅ |
| D10 | Share page: edit download limit, edit expiry | employee | `user/shares.spec.ts › share page: limit, expiry, preview, delete a file, ZIP, end the share` | ✅ |
| D11 | File preview modal | employee | same | ✅ |
| D12 | Delete a file (confirm) | employee | same | ✅ |
| D13 | Download all as ZIP | employee | same | ✅ |
| D14 | End share (confirm) | employee | same | ✅ |
| D15 | Add files to a live share | admin | `upload-resumable.spec.ts › resumable browser upload (Uppy/TUS) adds a file to an active share` | ✅ |
| D16 | Directed share via API, recipient download | admin, client | `share-flow.spec.ts › directed share + upload is visible in the recipient inbox and downloadable` | ✅ |
| D17 | Approval: held (recipient cannot see), approver queue, reject with reason, resubmit, approve | employee, admin, client | `user/shares.spec.ts › share approval › held, rejected with a reason, resubmitted, approved - then the recipient sees it` | ✅ |
| D18 | Approval: files added after approval wait for review, released | employee, admin, client | same | ✅ |
| D19 | Approval: the sender discards a held share | employee | `user/shares.spec.ts › share approval › the sender discards a held share` | ✅ |
| D20 | Approval: files added after approval discarded by the approver; the recipient never gets them | admin, client | `user/shares.spec.ts › share approval › held, rejected with a reason, resubmitted, approved - then the recipient sees it` | ✅ |

### Uploads

| # | Feature / state | Roles | Test | |
|---|---|---|---|---|
| E1 | Resumable (tus) upload in the browser | admin | `upload-resumable.spec.ts › resumable browser upload (Uppy/TUS) adds a file to an active share` | ✅ |
| E2 | A dropped connection resumes at the server offset | employee | `user/uploads.spec.ts › a connection dropped mid-file resumes at the server offset` | ✅ |
| E3 | Upload error state and Retry | employee | `user/uploads.spec.ts › an upload that keeps failing shows its error, and Retry finishes it` | ✅ |
| E4 | Remove a queued file before sending | employee | `user/uploads.spec.ts › a queued file is removed before sending` | ✅ |
| E5 | Over the storage quota: refused with the reason | employee | `user/uploads.spec.ts › an upload over the storage quota is refused with the reason` | ✅ |
| E6 | Folder upload keeps the tree (page + ZIP) | admin | `folder-upload.spec.ts › a picked folder keeps its structure on the share page and in the ZIP` | ✅ |
| E7 | Direct upload (below the threshold) through the edge | admin | `edge-behaviour.spec.ts › a direct upload lands through the edge proxy` | ✅ |
| E8 | The browser picks direct vs resumable by size | employee | ❌ (c) the e2e build forces resumable (`VITE_DIRECT_UPLOAD_THRESHOLD=1`); `frontend/tests/composables/useUpload.threshold.test.ts` | ❌ |
| E9 | Leaving the page mid-upload asks first | employee | ❌ (c) `frontend/tests/composables/useUploadLeaveGuard.test.ts` ("asks before an in-app navigation while uploads run") | ❌ |

### Public links

| # | Feature / state | Roles | Test | |
|---|---|---|---|---|
| F1 | Create a link on the share page (password, limit, notify), URL + QR | employee | `user/public-links.spec.ts › the owner creates a protected link; a visitor unlocks, downloads, and loses it on revoke` | ✅ |
| F2 | Visitor: locked page, wrong password, unlock | guest | same; `public-link.spec.ts › public link password unlock (UI) + download-limit exhaustion (API)` | ✅ |
| F3 | Visitor: download a file, downloads left, preview, ZIP | guest | same | ✅ |
| F4 | Owner told about the download (bell), sees the count, revokes; visitor sees "revoked" | employee, guest | same | ✅ |
| F5 | Exhausted link, ended share, unknown token | guest | `user/public-links.spec.ts › an exhausted link, an ended share and an unknown token each say so` | ✅ |
| F6 | Link while composing, to an address with no account; mailed; invite offer | employee, guest | `user/public-links.spec.ts › a link attached while composing is mailed to an address with no account` | ✅ |
| F7 | Brute-force lock after failures from several addresses | guest | ❌ (c) one browser is one address; `backend/tests/test_public_link.py::test_password_verify_locks_on_distributed_attack`, `::test_single_ip_failures_do_not_lock_link_for_everyone` | ❌ |
| F8 | Copy URL / download QR buttons | employee | ❌ (c) `frontend/tests/components/PublicLinkPanel.test.ts` ("shows a stored link again, with Copy") | ❌ |
| F9 | ZIP resume through the edge, charged once | guest | `edge-behaviour.spec.ts › a public ZIP download resumes through the edge and is charged once` | ✅ |

### Secrets

| # | Feature / state | Roles | Test | |
|---|---|---|---|---|
| G1 | Compose a secret: blockers, passphrase mismatch, link, sent screen | employee | `user/secrets.spec.ts › secrets composed in the UI › send a secret with a passphrase and a link; the recipient reveals it from Received` | ✅ |
| G2 | Recipient: Received box → reveal with passphrase | client | same | ✅ |
| G3 | One-view secret revealed once; sender sees the view, never the text | client, admin | `secrets.spec.ts › secrets › an account recipient reveals a one-view secret once` | ✅ |
| G4 | Link reveal with passphrase (`/s#token`), token never in an API URL | guest | `secrets.spec.ts › secrets › a passphrase-protected link reveals through /s#token` | ✅ |
| G5 | Sender: replace the link (old one dead), remove it, Burn now, Ended filter | employee | `user/secrets.spec.ts › secrets composed in the UI › the sender replaces and removes the link, burns the secret, and filters by state` | ✅ |
| G6 | Compose a request with a link; the target sees it under Requests; cancel; closed link | employee, client, guest | `user/secrets.spec.ts › secrets composed in the UI › a secret request is composed with a link, then cancelled` | ✅ |
| G7 | Answer a request (account) and open the answer | client, admin | `secret-requests.spec.ts › secret requests › an account target answers and the requester opens the answer` | ✅ |
| G8 | Answer a link request anonymously (`/r#token`), once; requester passphrase | guest, admin | `secret-requests.spec.ts › secret requests › a link request with the requester passphrase is answered through /r#token` | ✅ |
| G9 | Destroyed after N wrong passphrases (policy) | guest | ❌ (c) `backend/tests/test_secret_reveal.py::test_burn_mode_through_a_link_ends_the_secret_when_it_was_the_only_way_in` | ❌ |

### Notifications, legal pages

| # | Feature / state | Roles | Test | |
|---|---|---|---|---|
| H1 | Bell updates live (SSE), opens the share, Delete all | client | `user/notifications.spec.ts › the bell lights up live for a new share, opens it, and clears` | ✅ |
| H2 | Mailed manage link: save a channel, one-click unsubscribe + Undo, invalid link | guest | `user/notifications.spec.ts › the mailed manage link saves a channel, unsubscribes in one click, and undoes it` | ✅ |
| H3 | Legal page renders admin HTML safely | guest | `legal-page-xss.spec.ts › admin-authored HTML on the legal page cannot execute script` | ✅ |
| H4 | Legal page keeps its markup | guest | `legal-page-xss.spec.ts › the sanitiser keeps the markup a legal page actually needs` | ✅ |
| H5 | Edge: security headers, no-cache index, tus hook refused, CSP | guest | `edge-behaviour.spec.ts › the SPA shell carries its security headers` and the four tests after it | ✅ |
