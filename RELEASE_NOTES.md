# file:Heron v2.10.0

Adds the **scan guard**: automatic, configurable blocking of sources that probe
your instance for secrets and config files. One migration, no host step, and
**nothing changes until you switch it on** — it ships disabled.

---

## Before you update

Safe to take unattended. The new feature is off, the new table starts empty, and
no existing behaviour, API or setting changes. There is a migration
(`202608080001`, the `ip_blocks` table), so rolling back past this release needs
the usual `alembic stamp` recovery.

---

## Scan guard

Every self-hosted instance on a public address gets scanned constantly. On the
reference instance that was **1,664 requests from 93 addresses over two months** —
`/.env`, `/.git/config`, `/.aws/credentials`, `/wp-config.php`, `/.stripe/` —
people looking for a leaked secret file. Those already returned 404 and appeared
in the error log. The scan guard is the switch that makes them stop arriving.

Find it at *Settings → Scan guard*. **It ships off**, and everything about it is
yours to configure: which signals count, how many offences over what period, how
long a block lasts, whether repeat offenders get longer, and whether you hear
about it never, once a day, or on every block.

Three signals, each independent:

- **Probes for files that don't belong here** — on once you enable the guard. One
  hit is enough: these paths have no legitimate use, and a scanner typically
  tries a hundred of them in seconds (113 distinct paths in 19 seconds was
  measured). There is no list to maintain, though you can add your own patterns.
- **Repeated unknown API paths** — off. An expired share link also returns 404,
  so this one can reach a real recipient. Public share links are never counted
  regardless, and it only fires once a source has tried many *different* paths.
- **Repeated sign-in failures** — off. Brute-force blocking, with the same
  optional escalation to a whole network.

Blocked sources appear on the same page with the reason, the hit count, when the
block expires, and a Release button. You can also block an address yourself.

### The safeguards matter more than the feature

This is the only control in file:Heron that refuses to serve someone, so it is
built around what it will *not* do:

- **Signed-in users are never blocked.** Not one of those 1,664 requests came
  from a logged-in session, so this costs nothing — and it means you cannot lock
  yourself out by using your own product.
- **Private, loopback and allowlisted addresses are never blocked**, whatever the
  settings say. Put your office address in the allowlist.
- **No permanent blocks anywhere.** Every entry expires on its own, so a mistake
  heals without anyone intervening.
- **A blocked request gets an ordinary 404**, identical to any other miss, so a
  scanner learns nothing about what tripped or when.
- **If Redis is unavailable, nobody is blocked.** The guard protects nothing that
  was not already returning 404, so it fails open rather than risk an outage.

### Blocking whole networks is off, and worth thinking about

The guard can escalate from a single address to its whole /24. On the reference
instance that would have covered 59% of the traffic — but a /24 is 256 addresses,
and it may be a customer's office, a mobile carrier, or a mail-security gateway
that opens share links from many addresses at once. Enable it deliberately, not
by default.

## Smaller things

- Blocked-source records carry an IP, so they get a retention window like every
  other such table, prunable at *Settings → Advanced*.
- Anomaly detection has always been advisory. It still is by default, but if you
  turn on the sign-in-failure signal it can now act. The documentation that said
  it "never auto-blocks" has been corrected rather than quietly deleted.
