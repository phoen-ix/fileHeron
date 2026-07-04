# file:Heron v1.59.0

**Single sign-on (OIDC) fixes.** The third follow-up release from the code audit.
It fixes account-linking over SSO and removes a group-to-role mapping that never
worked. Roles stay managed inside fileHeron. This release includes a small
automatic database change (applied on startup - no host step).

## What's fixed

- **Connecting your account to SSO now works.** From **Account -> connect single
  sign-on**, linking your existing fileHeron account to an identity provider was
  failing: the provider's browser redirect back to fileHeron couldn't be
  authenticated, so the link never completed. The return trip is now authenticated
  by a tamper-proof signed token, so connecting works end to end.

## What's changed

- **Roles are managed in fileHeron, not by your identity provider.** The SSO
  provider form had "groups claim", "admin groups", and "employee groups" fields
  that were saved but never actually did anything. They have been removed. A user's
  role (admin / employee / client) is set inside fileHeron - when an admin invites
  them or edits their account - and signing in via SSO never changes it. In
  particular, **a fileHeron admin always stays an admin**, and no identity-provider
  group can grant or remove admin.
- If you had those group fields filled in, no action is needed - they were doing
  nothing, and the values are dropped by this update.

## Notes

- This release drops three unused columns from the SSO-providers table. The change
  runs automatically when the backend starts (as with every migration) - there is
  no host step, and no other data is affected.
- Deploy from the in-app Update banner as usual.
