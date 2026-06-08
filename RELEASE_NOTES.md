# file:Heron v1.51.0

**A clearer access-policy picker.** The *who-can-create* policy for **API tokens**
and **public links** offered two options that did exactly the same thing -
*Admins only* and *Disabled*. Because admins always keep an escape hatch, picking
"Disabled" blocked the same people as "Admins only" and allowed the same people.
The duplicate is gone.

## What's new

- **"Disabled" removed from both policy pickers.** `/admin/settings/api-tokens` and
  `/admin/settings/public-links` now show three base modes: **Everyone**,
  **Employees + admins**, and **Admins only**. The additive user/group allowlist
  still works on top of any mode, exactly as before.
- **Same lockdown, fewer choices.** To restrict creation to admins only, pick
  **Admins only** and leave the allowlist empty - that already means "no one but
  admins (plus anyone you explicitly allowlist)." Nothing you could express before
  is lost.
- **No surprises on upgrade.** Any deployment currently set to "Disabled" is
  migrated to the identical **Admins only** automatically, so who-can-create does
  not change for anyone.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`.
- One re-runnable database migration rewrites a stored policy value from `disabled`
  to `admins_only` for the API-token and public-link policies; no host step.
- Belt-and-suspenders: even before the migration runs, the backend treats a
  leftover `disabled` value as `admins_only`, so an imported config backup carrying
  the old value never silently loosens access.
- Rolling back to a pre-v1.51 image is safe; the older build still understands the
  `admins_only` value.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.51.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.51.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.51.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.51.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.51.0`

Click **Update** in `/admin/system` to roll forward.
