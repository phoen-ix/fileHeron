# E2E coverage, part c: use cases (`tests/usecases/`)

Parts a and b map every FEATURE to a test. This part maps every HAND-OVER: a chain in which one
person gives something to another - an account, a mailed link, a token, a setting - and the second
person uses it.

**Why it exists.** The feature specs set their preconditions up through the API and often read the
outcome through it too, so what one page produced was rarely what the next one consumed. Read from all
33 specs on 2026-11-21: of 22 hand-overs, 2 were followed to the second person in the browser, 5
partly, 15 not; no admin spec used the sign-in form. An account the admin created in the UI was
"signed in" with an API call; a forced password reset ended at "a token is shown".

**The rules** (the last one pinned by `backend/tests/test_frontend_test_hygiene.py`):

- Every person acts in the browser and signs in through the FORM - the admin too
  (`browserOf(browser, THE_ADMIN)`).
- What is handed over is the SAME thing that is used: the link on the admin's screen, or the mail to
  that address (`mailed` / `mailedToken`), never "the newest token in the log".
- The outcome is read on the affected person's screen.
- A use-case spec calls no API and imports only `./lib`. Background facts a use case is not about
  (a second account, a file already sent, a setting to put back) come from `given` in
  `tests/usecases/lib.ts`, the one file that does. One exception, by nature: the holder of an API
  token is a program, so `asTokenHolder` reads what the API tells it.

**A failing chain is a product finding.** What the 27 chains found on 2026-11-21, each fixed with its
chain as the test: the forced password reset handed the admin a bare token (U4); the bell asked for
data a person who must still enrol in two-factor is refused, on every page (U17); a token an admin
switched off looked untouched in its owner's list (U25); a list filtered to "Open" told someone
whose request had just been answered that they had never asked (U24); a quarantine release restored
a share without telling its recipients (U15, backend test). Three more were the owner's call and
were decided the same day: an activated invitation promised an SSO sign-in the account could not do
and now hands over a set-password link (U3); the share form sent during maintenance mode and left an
empty share (U20); the uploader was never told how a quarantine ended (U15).

- **Run:** `npx playwright test tests/usecases` against a stack from `scripts/e2e.sh`.
- **Items:** 27 hand-overs - ✅ 26 · ❌ 1 (c) - and 7 visibility chains (✅ 7), in 39 tests, plus 5 left out below.

## Accounts - `accounts.spec.ts`

| # | Hand-over | Followed to | Test |
|---|---|---|---|
| U1 | Admin invites an employee with a group and a client | the invitee opens THEIR mail, registers, lands in the employee app with that group and client, signs out and in again; the admin sees the account | `U1 the invitee registers from their own mail and lands in the app their role gets` |
| U1 | "New link", "Resend" | the old link is dead for the invitee, the new one opens | `U1 a new link replaces the old one, and a resent mail replaces that` |
| U2 | Admin creates an account with a password | the person signs in through the form, changes the password; the old one is refused | `U2 an account the admin creates with a password signs in with it, and changes it` |
| U3 | Admin activates a pending invitation | the dialog says the account has no password; the admin is shown a set-password link; the person opens THAT link, sets a password and is in as an employee; the link works once; the mailed invitation is dead | `U3 an invitation the admin activates: the admin is handed the link with which the person gets in` |
| U4 | Admin forces a password reset | the person opens the link the admin was given, sets a password, signs in; the old password is refused, their other device is signed out | `U4 a reset the admin forces: the person opens the link the admin was given` |
| U5 | A person locks themselves out | the warning mail reaches them; the admin unlocks; they sign in | `U5 a person locks themselves out, is warned by mail, and signs in once the admin unlocks them` |
| U6 | Admin disables, then re-enables an account | the person's open tab is signed out and the form refuses them; re-enabled, they sign in | `U6 a disabled account is signed out and refused, and signs in again once re-enabled` |
| U7 | Admin changes a role | the person's app follows (client to employee and back) | `U7 a role the admin changes is the app the person gets` |
| U8 | Admin changes an email address, at once and by confirmation link | the person signs in with the new address; the confirmation link is the one in their mail | `U8 an address the admin changes is the one that signs in: at once, and by the confirmation link` |
| U9 | Admin revokes one session, then all | that device is signed out, then the rest | `U9 a session the admin revokes signs that device out; "Revoke all" signs out the rest` |
| U10 | Admin erases a person | they cannot sign in; their share is off the recipient's list, and under "Any state" it is revoked, from "[erased]", with no file | `U10 an erased person can no longer sign in, and what they sent is gone for its recipient` |

## Sharing - `sharing.spec.ts`

| # | Hand-over | Followed to | Test |
|---|---|---|---|
| U11 | Admin adds a client to a group, then removes them | the employee can address the client and sends to the group; the client downloads; removed, the share is gone for them and they cannot be addressed | `U11 a client the admin adds to a group can be sent to, and loses the group's shares when removed` |
| U12 | An employee sends to two people | one opens it FROM THE MAIL (sign-in, then the share), one from the bell; both download; the sender's page names both and counts "2 of 5" | `U12 a share is opened from its mail and from the bell, downloaded, and the sender sees it was` |
| U13 | A client hands files in | an employee downloads them from Received | `U13 files a client hands in are downloaded by an employee from Received` |
| U14 | A share to an address without an account, with a password and a limit | the link in THEIR mail, a wrong then the right password, downloads to the limit, refused after; the sender's bell and mail say it was downloaded | `U14 someone without an account opens the mailed link, unlocks it, and downloads up to the limit` |
| U15 | The scanner stops a file; the admin releases it | the sender's bell names the file; the recipient's share is off their list and, under "Any state", revoked with nothing to download; released, the recipient downloads, and the sender is told in the bell (its entry opens the share) and by mail | `U15 a file the scanner stops: the sender is told, the recipient gets it once the admin releases it, and the sender is told that too` |
| U15 | ... the admin purges it | the sender is told in the bell and by mail, their share is empty; the recipient's is revoked, with no file, under "Any state" | `U15 a file the scanner stops and the admin purges is gone for good: the sender sees an empty share, the recipient none` |
| U16 | Admin marks a group as a company inbox | an employee outside it can address it, and not a plain group | `U16 a group the admin marks as a company inbox can be addressed by an employee outside it` |

The e2e stack runs without ClamAV, so `given.infectedShare` quarantines the file with the worker's own
call after the share was announced. The order a real scan produces - flagged BEFORE the announcement,
released later - is `backend/tests/test_quarantine_admin.py::test_releasing_a_file_announces_the_share_it_restores`.

## Policies - `policies.spec.ts`

Set by the admin in the UI, read in the browser of the person it applies to.

| # | Hand-over | Followed to | Test |
|---|---|---|---|
| U17 | Two-factor required for clients | the client is held on the setup page, FINISHES enrolment, gets the app and the bell, and signs in with a code | `U17 two-factor required for a role: the person is sent to enrol, finishes, and signs in with a code` |
| U18 | Share approval switched on | the sender's share is held; the recipient's list holds it in NO state; the approver approves; the recipient downloads | `U18 share approval switched on: a new share is held until an approver decides, then reaches its recipient` |
| U19 | Public links for admins only, one employee allowed | the option is gone on one employee's form and there on the other's | `U19 … › public links for admins only, with one employee allowed` |
| U19 | API tokens for admins only, one employee allowed | one account page says tokens are restricted, the other offers "Create token" | `U19 … › API tokens for admins only, with one employee allowed` |
| U19 | Secrets on, then off | "Secrets" appears in and leaves a person's menu | `U19 … › secrets switched on, then off` |
| U19 | Changing one's own email allowed, then not | the field appears on and leaves a person's account page | `U19 … › changing one's own email allowed, then not` |
| U20 | Maintenance mode on, then off | a person arriving sees the banner; the new-share form and "+ Add files" on a share they already sent both name the reason and will not send; a form that was already open gets the server's refusal with the reason; off, both send | `U20 maintenance mode: people see the banner and neither form will send; switched off, both do` |
| U21 | File preview off | a recipient is no longer offered Preview | `U21 file preview switched off: a recipient is no longer offered Preview` |
| U21 | A storage quota set on a person's page | their next upload is refused with the reason | `U21 a storage quota the admin sets on a person stops their next upload, with the reason` |
| U22 | A logo enabled for the sign-in page | a visitor sees it there | `U22 a logo the admin enables for the sign-in page is what a visitor sees there` |

The login banner (the other half of U22) is read on a visitor's screen by
`admin/site.spec.ts › login banner: shown on the sign-in page while on, gone when off`, and again in U26.

## Integrations - `integrations.spec.ts`

| # | Hand-over | Followed to | Test |
|---|---|---|---|
| U23 | Admin sets up a sign-on provider in the UI (the stand-in IdP) and tests it | a client signs in with its button and is linked; switched off, the button is gone for a visitor | `U23 a sign-on provider the admin sets up in the UI signs a client in, until the admin switches it off` |
| U24 | A secret request | the person asked follows the link in THEIR mail, signs in, answers; the requester finds the answered request in "Asked by me" as it opens, and opens the answer | `U24 a secret request reaches the person asked by mail; their answer is opened by the one who asked` |
| U25 | Admin makes an API token for someone | the token authenticates as its owner, who sees it on their account page; switched off, it is refused and the owner's page says so; reactivated, it works; revoked, it is refused and gone | `U25 an API token the admin makes for someone works, stops when switched off, and ends when revoked` |
| U26 | A configuration backup with accounts, restored over later changes | the preview names the account that will be removed; a visitor sees the banner as backed up; the renamed person is signed out and signs in under their old name; the later account is refused; the restoring admin's own notification choice survives | `U26 a configuration backup restored over later changes: settings and names are back, later accounts are gone` |
| U27 | Mail log "Resend" | ❌ (c) the e2e stack has no SMTP server, so every logged mail is `dev_fallback`, which cannot be resent by design; the resend and its re-minted footer link are `backend/tests/test_mail_log.py` | - |

U26 runs last in its file: an import with accounts ends EVERY session on the stack, the helpers'
cached admin token included (`everySessionEnded`).

## Who sees what - `visibility.spec.ts`

Not hand-overs: the same rules read on the screen of someone who must NOT see a thing, beside
someone who does. The routes behind these screens are asked directly - every kind of person, every
share and file state - in `backend/tests/test_visibility_*.py` and
`test_admin_routes_refuse_non_admins.py`; these are the rules as a person meets them. "Not in my
list" is always checked with **Any state** selected (`receivedUnderAnyState`): the list opens on
"Active", where a held or an ended share is missing whatever the server gives out.

| # | Rule | Seen by | Test |
|---|---|---|---|
| V1 | The other recipients of a share, and the name of a group one is not in | the sender sees both people and the group; a direct recipient sees no other person and no group, on the page and in Received; a group member sees their group and no person | `V1 a recipient sees neither the other recipients nor a group they are not in; the sender sees them all` |
| V2 | Group membership is read when asked | a member downloads; removed by the admin, the button on their still-open page is refused, the address is refused, the share is in their list in no state; another member is untouched | `V2 a group member the admin removes can no longer open the share, by its address either` |
| V3 | A share held for approval | the recipient: not listed in any state, refused by address; an employee who approves nothing: refused, nothing in Approvals | `V3 a share held for approval is nowhere for its recipient: not listed in any state, not by its address` |
| V4 | A share is for whom it was sent to | an EMPLOYEE and a client it was not sent to: refused by address, not listed in any state | `V4 a share is closed to an employee and to a client it was not sent to` |
| V5 | What a client hands in | an employee and a client in the sender's group see it, with the sender's name, and download it; a client outside sees nothing | `V5 what a client hands in: staff and the client's group see it and who sent it; a client outside does not` |
| V6 | Whom a client can find | their own employee; not another employee, not another client by name or by address | `V6 a client finds the employee they work with, and no other client or employee` |
| V7 | The Received filter "Via group" | offers the groups the viewer is in and no other, and narrows the list (it was empty for everyone but an admin) | `V7 the Received filter "Via group" offers the groups I am in, and narrows the list to one` |

## Left out

| Hand-over | Why |
|---|---|
| The first-run `/setup` wizard | (d) the stack's admin exists before the first test; `backend/tests/test_setup_wizard.py`, `frontend/tests/views/Setup.test.ts` |
| A valid `/verify-email` link | (d) every account the stack can make is verified on creation or by its invite; `backend/tests/test_token_single_use.py` |
| A real IMAP fetch | (a) needs a mailbox; `backend/tests/test_imap_*.py` |
| Scan-guard auto-blocks | (d) only a public address is ever counted, and the stack has none; `backend/tests/test_scan_guard*.py` |
| The update and rollback dialogs carried through | (b) they replace the stack that runs the test; `admin/system.spec.ts` covers the dialogs up to the hand-off |
