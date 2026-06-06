# file:Heron v1.24.0

**Share approval (four-eyes).** An optional, admin-controlled gate: when enabled,
a newly created share waits in **pending approval** until a designated approver
approves it (releasing it to its recipients) or rejects it back to the sender.
Off by default — when off, shares go live immediately, exactly as before.

## What's new

- **Approval policy, fully admin-configurable** at *Settings → Share approval*:
  - **Who approves** — admins only, employees + admins, or specific users/groups.
  - **Which shares need approval** — outbound only, all shares (including client
    uploads), or only outbound shares to clients.
  - **Approvers' own shares** — auto-approve (no deadlock) or require a different
    approver. No one can ever approve their own share.
  - **Content review** — whether approvers may open the pending files to inspect
    them before deciding, or decide on metadata only.
- **Approvals queue** — approvers get a new **Approvals** entry in the top nav
  listing everything waiting on them; click through to review and **Approve** or
  **Reject** (with an optional reason).
- **Sender experience** — a held share shows "pending approval"; a rejected share
  shows the reason and a **Resubmit** button. The uploaded files are kept on
  rejection, so resubmitting doesn't mean re-uploading.
- **Notifications** (email + in-app, EN/DE) to the approvers ("a share needs your
  approval") and back to the sender ("approved" / "rejected").

## Good to know

- **Off by default.** Existing installs are unaffected until an admin enables it.
- A pending or rejected share is **invisible to its recipients** and to any public
  link until it's approved. Previews/downloads of pending files are limited to
  approvers, and only when content review is turned on.
- **No self-approval, ever** — even an admin can't sign off their own share when
  approval is required of it.

## Upgrade notes

- **One small migration** widens the share `state` column (to fit the new
  `pending_approval` state) and adds approval-tracking columns. It's re-runnable
  and applied automatically on update; no existing data changes.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.24.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.24.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.24.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.24.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.24.0`

Click **Update** in `/admin/system` to roll forward.
