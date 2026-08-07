# file:Heron v2.10.3

Housekeeping for **new and previously-broken installs**. If you are already on
v2.10.2 and your updater is working, this changes nothing for you — take it
whenever convenient.

No migration, no host step, no settings or API changes.

---

## The updater's host paths are now set for you

v2.10.1 fixed the bug that made the in-app updater single-use: it ran
`docker compose` from inside a container and let `${PWD}` decide two host paths,
so each successful update misconfigured the shim it recreated and the *next*
update failed with *"executor crashed (exit 1) without writing status"*.

That fix relies on the updater getting those paths right in code. This release
adds a second, independent layer: the paths are now written into `.env`
directly, so the `${PWD}` fallback never applies at all and no version of the
updater can get it wrong.

- `.env.example` sets them instead of listing them as an optional override.
- `install.sh` writes your **actual** install directory — so `--dir=` is
  honoured rather than assuming `/opt/fileHeron`.
- It rewrites them on **every** run, not just fresh installs. If you were bitten
  by the original bug, your `.env` still contains `/workspace`, and re-running
  the installer repairs it while leaving every other value alone.

## If your updater is currently failing

Re-run the installer over your existing checkout, or set the two values by hand
in `.env` and recreate the shim:

```
UPDATER_HOST_WORKSPACE=/opt/fileHeron
UPDATER_HOST_STATE=/opt/fileHeron/data/updater
```

```
cd /opt/fileHeron && docker compose up -d updater-shim
```

Then update as usual. Nothing else needs to change.
