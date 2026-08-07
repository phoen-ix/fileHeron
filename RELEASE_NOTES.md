# file:Heron v2.10.1

Fixes a bug in the in-app updater that made it **single-use**: every successful
update quietly broke the next one. If your last update failed with *"executor
crashed (exit 1) without writing status"*, this is why — and the recovery is
below.

No migration, no host step, no setting changes.

---

## The updater broke the update after itself

The updater runs `docker compose up -d` from inside a container whose working
directory is `/workspace`. Two host-path settings in `docker-compose.yml` fall
back to `${PWD}`, so the `updater-shim` that this command recreates was left
believing the host state directory is `/workspace/data/updater` instead of the
real one.

Nothing looked wrong at the time — the shim's own mount still resolved correctly.
The damage showed up on the *next* update: the shim launched the executor
pointed at a directory that did not exist, Docker obligingly created it empty,
and the executor exited before it could even report why.

So the pattern was: install an update, and the following one fails. A first
update after a hand-rolled `docker compose up` would work, and the second would
not.

Both sides are fixed. The executor now pins every host path explicitly instead
of letting `${PWD}` decide, and the shim hands over the paths it already knows
rather than making the executor reconstruct them. There is a regression test
over both.

## If your update is currently failing

The fix cannot install itself — the broken shim is the thing that would run it.
Recreate the shim from your compose directory first, which takes a second and
touches nothing else:

```
cd /opt/fileHeron && docker compose up -d updater-shim
```

Then update from **Admin → System → Update** as usual. Once you are on v2.10.1
the problem does not come back.

You may also find an empty `/workspace/data/updater` directory on your host,
created by Docker during a failed attempt. It is junk and safe to delete.

## Note on v2.10.0

v2.10.0 itself is sound — it is the release the old updater struggled to
*install*, not the cause. Everything in it (the [scan
guard](https://github.com/phoen-ix/fileHeron/releases/tag/v2.10.0)) is included
here, so update straight to v2.10.1.
