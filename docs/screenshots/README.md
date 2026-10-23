# Screenshots

Every screen of file:Heron, with a seeded demo organisation. They are made by the docs tour
(`e2e/docs/`) through `scripts/docs-screenshots.sh` - see
[CONTRIBUTING.md](../../CONTRIBUTING.md#docs-screenshots). The admin shots show the page without
the sidebar, which the overview and the user list show.

The tour also opens these and checks they render, without a picture - they are settings forms
built like the ones shown: the policy tabs of Sessions, API tokens, Secrets and Quarantine, the
alert settings of Errors & alerts, Auto-block rules, Legal pages, Files & transfers, Email change,
Inbound mail (IMAP), Anomaly detection and the new-SSO-provider form. Not visited: an inbound
mail's detail page (it needs a real IMAP mailbox) and the SSO second-factor page (it needs an
identity provider).

## Sign in (`/login`)

Email and password; with SSO providers or passkey sign-in switched on, their buttons appear here too.

<img src="login.png" alt="Sign in: email and password; with SSO providers or passkey sign-in switched on, their buttons appear here too." width="800">

## Sign in with two-factor authentication

With 2FA on, the password step reveals one field for an authenticator code or a recovery code, or a passkey instead.

<img src="login-2fa.png" alt="Sign in with two-factor authentication: with 2FA on, the password step reveals one field for an authenticator code or a recovery code, or a passkey instead." width="800">

## First-admin setup (`/setup`)

A fresh instance without an admin opens this wizard; `install.sh` prints its `/setup?token=…` link.

<img src="setup.png" alt="First-admin setup: a fresh instance without an admin opens this wizard; `install.sh` prints its `/setup?token=…` link." width="800">

## Accept an invitation (`/register/…`)

The link in an invitation mail: the invitee picks a display name and a password.

<img src="register.png" alt="Accept an invitation: the link in an invitation mail: the invitee picks a display name and a password." width="800">

## Forgot password (`/forgot-password`)

Asks for the account's email and mails a reset link.

<img src="forgot-password.png" alt="Forgot password: asks for the account's email and mails a reset link." width="800">

## Reset password (`/reset-password/…`)

The reset link's page: a new password, checked against the strength rules.

<img src="reset-password.png" alt="Reset password: the reset link's page: a new password, checked against the strength rules." width="800">

## A spent or invalid link (`/verify-email/…`)

What an expired or already used email link shows - the same card serves the email-change confirm and cancel links.

<img src="verify-email-invalid.png" alt="A spent or invalid link: what an expired or already used email link shows - the same card serves the email-change confirm and cancel links." width="800">

## Public link, locked (`/d/…`)

An anonymous recipient first enters the link's password.

<img src="public-link-locked.png" alt="Public link, locked: an anonymous recipient first enters the link's password." width="800">

## Public link, unlocked

The sender's message, the files and how many downloads the link has left.

<img src="public-link.png" alt="Public link, unlocked: the sender's message, the files and how many downloads the link has left." width="800">

## Secret link (`/s#…`)

A secret sent as a link: the recipient sees what it is before revealing it, which spends a view.

<img src="public-secret.png" alt="Secret link: a secret sent as a link: the recipient sees what it is before revealing it, which spends a view." width="800">

## Secret request link (`/r#…`)

Someone without an account answers a secret request; the answer is encrypted for the requester.

<img src="public-secret-request.png" alt="Secret request link: someone without an account answers a secret request; the answer is encrypted for the requester." width="800">

## Manage subscriptions (`/manage-notifications/…`)

The link at the foot of every notification mail: per-category email choices without signing in.

<img src="manage-notifications.png" alt="Manage subscriptions: the link at the foot of every notification mail: per-category email choices without signing in." width="800">

## Imprint (`/imprint`)

The imprint and privacy pages show what an admin wrote under Branding & legal.

<img src="legal-imprint.png" alt="Imprint: the imprint and privacy pages show what an admin wrote under Branding & legal." width="800">

## Not found

Any unknown address.

<img src="not-found.png" alt="Not found: any unknown address." width="800">

## Home (`/`)

The landing page, unless an admin switched it off or the user picked another default page.

<img src="home.png" alt="Home: the landing page, unless an admin switched it off or the user picked another default page." width="800">

## New share (`/share/new`)

Files, subject, message, people and groups as recipients, the expiry and an optional public link.

<img src="share-new.png" alt="New share: files, subject, message, people and groups as recipients, the expiry and an optional public link." width="800">

## A share (`/share/…`)

Its files with their scan verdicts, preview, download and delete actions, and the public link panel.

<img src="share-page.png" alt="A share: its files with their scan verdicts, preview, download and delete actions, and the public link panel." width="800">

## The same share in German

Every page follows the user's language; English and German ship.

<img src="share-page.de.png" alt="The same share in German: every page follows the user's language; English and German ship." width="800">

## Ending a share

Ending a share early asks first, because it deletes the uploaded files.

<img src="share-page-end-dialog.png" alt="Ending a share: ending a share early asks first, because it deletes the uploaded files." width="800">

## Sent (`/outbox`)

Every share the user sent, here filtered to all states, so active, expired and revoked shares show.

<img src="outbox.png" alt="Sent: every share the user sent, here filtered to all states, so active, expired and revoked shares show." width="800">

## Sent, empty

A client who has not sent anything yet.

<img src="outbox-empty.png" alt="Sent, empty: a client who has not sent anything yet." width="800">

## Sent on a phone

The lists scroll sideways inside their own frame on a narrow screen.

<img src="outbox.mobile.png" alt="Sent on a phone: the lists scroll sideways inside their own frame on a narrow screen." width="800">

## Received (`/inbox`)

A client's inbox with the shares sent to them and to their groups.

<img src="inbox.png" alt="Received: a client's inbox with the shares sent to them and to their groups." width="800">

## Approvals (`/approvals`)

With share approval on, held shares wait here for an approver.

<img src="approvals.png" alt="Approvals: with share approval on, held shares wait here for an approver." width="800">

## Secrets (`/secrets`)

Sent and received secrets and secret requests, with their state.

<img src="secrets.png" alt="Secrets: sent and received secrets and secret requests, with their state." width="800">

## New secret (`/secrets/new`)

The text, who may read it, how many views and until when, an optional passphrase and link.

<img src="secrets-new.png" alt="New secret: the text, who may read it, how many views and until when, an optional passphrase and link." width="800">

## A secret (`/secrets/…`)

The sender's view: recipients, views used and the link.

<img src="secrets-detail.png" alt="A secret: the sender's view: recipients, views used and the link." width="800">

## Request a secret (`/secrets/requests/new`)

Ask someone to send a password or other secret back.

<img src="secrets-request-new.png" alt="Request a secret: ask someone to send a password or other secret back." width="800">

## A secret request (`/secrets/requests/…`)

Who was asked, until when, and the answer once it arrives.

<img src="secrets-request-detail.png" alt="A secret request: who was asked, until when, and the answer once it arrives." width="800">

## Account (`/account`)

Profile, language, landing page, password, sessions, notifications, passkeys and API tokens.

<img src="account.png" alt="Account: profile, language, landing page, password, sessions, notifications, passkeys and API tokens." width="800">

## Two-factor authentication (`/account/2fa`)

Turning on an authenticator app; recovery codes follow.

<img src="account-2fa.png" alt="Two-factor authentication: turning on an authenticator app; recovery codes follow." width="800">

## Overview (`/admin`)

The task-grouped sidebar, the setting search, attention tiles, service status and every admin page.

<img src="admin-overview.png" alt="Overview: the task-grouped sidebar, the setting search, attention tiles, service status and every admin page." width="800">

## Users (`/admin/users`)

Employees and clients with role, status, 2FA, storage and last sign-in.

<img src="admin-users.png" alt="Users: employees and clients with role, status, 2FA, storage and last sign-in." width="800">

## A user (`/admin/users/…`)

Role, status, quota, sessions, connections and the account actions.

<img src="admin-user-detail.png" alt="A user: role, status, quota, sessions, connections and the account actions." width="800">

## Groups (`/admin/groups`)

Groups address several people at once and act as company inboxes.

<img src="admin-groups.png" alt="Groups: groups address several people at once and act as company inboxes." width="800">

## A group (`/admin/groups/…`)

Its members and settings.

<img src="admin-group-detail.png" alt="A group: its members and settings." width="800">

## Audit log (`/admin/audit-log`)

Every security-relevant event, filterable by type, actor and date.

<img src="admin-audit-log.png" alt="Audit log: every security-relevant event, filterable by type, actor and date." width="800">

## File history (`/admin/file-history`)

Every file across all users, with its share, state and downloads.

<img src="admin-file-history.png" alt="File history: every file across all users, with its share, state and downloads." width="800">

## Analytics (`/admin/analytics`)

Transfer and usage figures over time.

<img src="admin-analytics.png" alt="Analytics: transfer and usage figures over time." width="800">

## Sessions (`/admin/sessions`)

Signed-in devices across all users, revocable one by one.

<img src="admin-sessions.png" alt="Sessions: signed-in devices across all users, revocable one by one." width="800">

## API tokens (`/admin/api-tokens`)

Every user's tokens, with scopes, expiry and last use.

<img src="admin-api-tokens.png" alt="API tokens: every user's tokens, with scopes, expiry and last use." width="800">

## Secrets (`/admin/secrets`)

Every secret's metadata - never its text - with the option to end it.

<img src="admin-secrets.png" alt="Secrets: every secret's metadata - never its text - with the option to end it." width="800">

## Secret requests (`/admin/secret-requests`)

Every open and closed secret request.

<img src="admin-secret-requests.png" alt="Secret requests: every open and closed secret request." width="800">

## Quarantine (`/admin/quarantine`)

Files the virus scanner flagged, to release or delete.

<img src="admin-quarantine.png" alt="Quarantine: files the virus scanner flagged, to release or delete." width="800">

## Error log (`/admin/error-log`)

Server errors and opted-in client errors, with request details.

<img src="admin-error-log.png" alt="Error log: server errors and opted-in client errors, with request details." width="800">

## Blocked sources (`/admin/ip-blocks`)

Addresses the scan guard blocked for probing or brute force.

<img src="admin-ip-blocks.png" alt="Blocked sources: addresses the scan guard blocked for probing or brute force." width="800">

## Inbound mail (`/admin/inbox`)

Replies, bounces and auto-replies the IMAP poll picked up.

<img src="admin-inbox.png" alt="Inbound mail: replies, bounces and auto-replies the IMAP poll picked up." width="800">

## Webhooks (`/admin/settings/webhooks`)

Outgoing event notifications to other systems, with delivery history.

<img src="admin-webhooks.png" alt="Webhooks: outgoing event notifications to other systems, with delivery history." width="800">

## Mail log (`/admin/mail-log`)

Every mail the instance sent or tried to send.

<img src="admin-mail-log.png" alt="Mail log: every mail the instance sent or tried to send." width="800">

## A mail (`/admin/mail-log/…`)

One mail with its masked body and delivery attempts, resendable.

<img src="admin-mail-detail.png" alt="A mail: one mail with its masked body and delivery attempts, resendable." width="800">

## General (`/admin/settings/general`)

Site URL, timezone, home page, message of the day, previews and share defaults.

<img src="admin-general.png" alt="General: site URL, timezone, home page, message of the day, previews and share defaults." width="800">

## Branding & legal (`/admin/settings/branding`)

Logo and name shown in the app and in mails.

<img src="admin-branding.png" alt="Branding & legal: logo and name shown in the app and in mails." width="800">

## Public links (`/admin/settings/public-links`)

Who may create public links, their limits, and recipients without an account.

<img src="admin-public-links.png" alt="Public links: who may create public links, their limits, and recipients without an account." width="800">

## Share approval (`/admin/settings/share-approval`)

The four-eyes rule: which shares are held and who approves them.

<img src="admin-share-approval.png" alt="Share approval: the four-eyes rule: which shares are held and who approves them." width="800">

## Encryption at rest (`/admin/settings/encryption`)

Encrypts every stored file with its own key; off by default.

<img src="admin-encryption.png" alt="Encryption at rest: encrypts every stored file with its own key; off by default." width="800">

## Sign-in policies (`/admin/settings/sign-in`)

Password rules, lockout and the sign-in options.

<img src="admin-sign-in.png" alt="Sign-in policies: password rules, lockout and the sign-in options." width="800">

## 2FA enforcement (`/admin/settings/twofa`)

Which roles and groups must use two-factor authentication, and passkey sign-in.

<img src="admin-twofa.png" alt="2FA enforcement: which roles and groups must use two-factor authentication, and passkey sign-in." width="800">

## SSO providers (`/admin/settings/sso`)

The OpenID Connect providers users can sign in with.

<img src="admin-sso.png" alt="SSO providers: the OpenID Connect providers users can sign in with." width="800">

## An SSO provider (`/admin/settings/sso/…`)

Preset, issuer, client credentials and redirect URI, with a connection test.

<img src="admin-sso-edit.png" alt="An SSO provider: preset, issuer, client credentials and redirect URI, with a connection test." width="800">

## Outgoing mail (`/admin/settings/email`)

The SMTP server, with a test mail.

<img src="admin-email.png" alt="Outgoing mail: the SMTP server, with a test mail." width="800">

## Email templates (`/admin/settings/email-templates`)

Every mail's subject and body per language, in a rich-text editor.

<img src="admin-email-templates.png" alt="Email templates: every mail's subject and body per language, in a rich-text editor." width="800">

## Data retention & storage (`/admin/settings/advanced`)

Retention windows and the other live-tunable settings.

<img src="admin-advanced.png" alt="Data retention & storage: retention windows and the other live-tunable settings." width="800">

## Backup & restore (`/admin/settings/backup`)

Export and import the configuration; both ask for the admin's password.

<img src="admin-backup.png" alt="Backup & restore: export and import the configuration; both ask for the admin's password." width="800">

## Maintenance mode (`/admin/settings/maintenance`)

Pauses sign-ins and transfers, for example before an update.

<img src="admin-maintenance.png" alt="Maintenance mode: pauses sign-ins and transfers, for example before an update." width="800">

## Scheduled tasks (`/admin/scheduled-tasks`)

Every background job with its schedule, last run and a run-now button.

<img src="admin-scheduled-tasks.png" alt="Scheduled tasks: every background job with its schedule, last run and a run-now button." width="800">

## Status & updates (`/admin/system`)

Service health, the running version, updates and rollback.

<img src="admin-system.png" alt="Status & updates: service health, the running version, updates and rollback." width="800">
