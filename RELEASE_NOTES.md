# file:Heron v2.12.1

**Corrects a claim v2.12.0 made and did not deliver.** v2.12.0's notes said a
failed backup or restore drill would e-mail whoever your error alerts go to. It
would not have. The alerting was wired into the wrong section of the systemd
unit files, and systemd silently ignored it.

No migration, no host step for the application, no setting changes. The images
are functionally identical to v2.12.0 — the fix is in two files that ship as
source, so **it only reaches you when you re-copy them** (below).

---

## A failed backup would still have told nobody

`OnFailure=` is a `[Unit]` directive. In `scripts/ops/fileheron-backup.service`
and `fileheron-restore-drill.service` it was placed at the end of `[Service]`,
where systemd drops it with a warning nothing surfaces unless you happen to run
`systemd-analyze verify`:

```
fileheron-backup.service: Unknown key 'OnFailure' in section [Service], ignoring.
```

The units looked correctly wired. `systemctl show fileheron-backup.service -p
OnFailure` returned **empty**. So a failed nightly backup would have remained
what it was before v2.12.0 — a `failed` unit and a journald line, with nothing
watching either. Which is precisely the situation that release set out to fix.

It was found by installing the units on a real host and reading systemd's own
verdict rather than trusting the file, and confirmed the same way:

| | before | after |
|---|---|---|
| `fileheron-backup.service` | `OnFailure=[]` | `OnFailure=[fileheron-alert-failure@…]` |
| `fileheron-restore-drill.service` | `OnFailure=[]` | `OnFailure=[fileheron-alert-failure@…]` |

## If you installed the v2.12.0 units, re-copy them

The unit files are **copies**, not symlinks — updating fileHeron does not touch
`/etc/systemd/system`, and the in-app updater never could. If you installed them
from v2.12.0, alerting is currently inert on your host:

```bash
# check first - empty output means alerting is not wired
systemctl show fileheron-backup.service -p OnFailure

sudo cp scripts/ops/fileheron-backup.service /etc/systemd/system/
sudo cp scripts/ops/fileheron-restore-drill.service /etc/systemd/system/
sudo systemctl daemon-reload
systemctl show fileheron-backup.service -p OnFailure   # now non-empty
```

Nothing else needs restarting, and the timers keep their schedule.

If you have not installed the units at all, there is nothing to do here — the
files are simply correct now.

> **Worth checking while you are there**, because a correctly wired alert still
> needs somewhere to go: alerting sends through the app's own SMTP config and
> its alert-recipient policy (**Admin → Settings → Error alerts**). With no
> explicit recipients it falls back to your enabled admins. And by design it
> cannot send if the backup failed *because* the stack is down — that case stays
> visible as `systemctl --failed` only.

---

## Everything else from v2.12.0 still applies

v2.12.1 changes nothing else. If you are coming from v2.11.0 or earlier, read
the [v2.12.0 notes](https://github.com/phoen-ix/fileHeron/releases/tag/v2.12.0)
— that release carries the upload-reaper fix, the security wave, migration
`202608150001`, and a **required host step** (`docker compose up -d tusd`)
without which the upload fix does nothing.
