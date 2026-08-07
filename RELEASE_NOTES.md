# file:Heron v2.10.2

**If you enabled the scan guard's sign-in-failure signal in v2.10.0, update.**
That signal could block you out of your own instance, including the login page.
This release fixes it. No migration, no host step.

---

## The sign-in-failure signal could lock you out

`auth_failure` counted **any** 401 or 403 as an attack. That is not brute force,
it is an expired session — and several perfectly normal things produce them:

- The admin system page and the notification bell use server-sent events.
  EventSource cannot send an authorisation header, so they authenticate with a
  signed token that expires after five minutes. Every reconnect after that is a
  legitimate 401 from an authorised admin. **Leaving the admin page open was
  enough to get blocked.**
- The web interface refreshes an expired session by design, which also produces
  401s.

Once blocked, the block covered the login route too, so the way back in was gone.

Brute force means repeated *credential submission*, so the signal now only
counts failures on the routes that actually accept credentials — sign-in,
password reset, invite registration, passkeys, SSO. A 401 anywhere else is
ignored. The event-stream endpoints are excluded outright.

## If you are locked out right now

From the host:

```
docker compose exec backend python - <<EOF
from app.database import SessionLocal
from app.services import settings as s
db = SessionLocal()
s.set_value(db, key=s.Keys.SCAN_GUARD_ENABLED, value="false", actor=None)
db.commit()
EOF
```

Then update, and re-enable the guard from *Settings → Scan guard*. Blocks expire
on their own within the hour in any case.

The probe-path signal — the one that handles the scanning traffic the feature
exists for — was never affected, and is safe to leave on.

## Also in this release

The in-app updater was single-use: every successful update misconfigured the
next one, which failed with *"executor crashed (exit 1) without writing
status"*. The executor ran `docker compose` from inside its own container and
let `${PWD}` decide two host paths, so the `updater-shim` it recreated pointed at
a directory that does not exist on the host. Both sides now pin the real paths.

If an update is failing that way, recreate the shim from your compose directory
first — the fix cannot install itself:

```
cd /opt/fileHeron && docker compose up -d updater-shim
```
