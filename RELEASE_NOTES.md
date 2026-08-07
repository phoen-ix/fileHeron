# file:Heron v2.11.0

Fixes for the scan guard, all found by reviewing it against real traffic rather
than by reasoning about it. **One is a live bug** that could keep a whole network
blocked for a week. No migration, no host step, and nothing changes unless you
have network escalation switched on.

---

## A network block could get stuck for a week

The rule that escalates from single addresses to a whole network counted its
evidence over the full lookback window (a week by default), while the block
itself only lasts an hour. So once a network had ever accumulated enough blocked
addresses, the block would expire — and then **a single new address would
re-block the entire network for another hour**, over and over, for the rest of
the week.

Evidence is now counted only since the last network block on that range ended.
Fresh evidence escalates; stale evidence does not.

## IPv6 grouping is now a setting — and the default did not change

IPv6 escalation groups by /64, and on a real instance that means it can never
fire: every scanner arrived from its own /64, so the threshold was never met.
The obvious fix is to group wider. **The obvious fix is wrong**, and the
measurement is worth stating because it is counter-intuitive:

The one /48 that did group turned out to be a hosting provider's VPS pool that
gives **one /64 per customer** — so grouping there would have blocked up to
65,536 unrelated customers to stop two. Hetzner and Vultr allocate the same way,
and OVH and Linode put several customers inside a single /64. **Prefix length is
not a measure of how many people you are blocking.**

So the grouping is now adjustable, the default stays /64, and /48 is not offered
at all — the widest setting is /56. Widen it only if you know the range belongs
to one operator; the settings page says so next to the control.

## Smaller fixes

- **A wide network block could have refused the server's own health check.** The
  "never block a private or loopback address" rule was applied when blocks were
  created but not when requests were served, so a hand-entered or very wide range
  could have taken out the frontend and the upload service. Now checked on both.
- **Changing the grouping releases existing network blocks.** They are stored
  under the grouping in force at the time, so leaving them behind would silently
  discard evidence and could leave a hidden block that survived releasing the
  visible one.
- **The "notify on every block" setting now actually notifies.** It shipped with
  no implementation behind it. The daily-digest option is removed rather than
  left as decoration; it can return when there is something behind it.
- **Fewer false credential-stuffing alerts.** A source that also signed in
  *successfully* during the window is no longer reported. A real attacker never
  gets in; a shared office address does it all day.
- **`scripts/unblock_ip.py`** — release blocks from the host when you cannot
  reach the admin page, which is exactly the situation a mistaken block creates:

  ```
  docker compose exec backend python scripts/unblock_ip.py --list
  docker compose exec backend python scripts/unblock_ip.py --all
  ```

## A correction

v2.10.0's documentation said an administrator could have the scan guard
auto-block a source flagged for credential stuffing. **That was never true** —
no such wiring existed. The claim has been removed rather than quietly patched
over, and automatic blocking from stuffing detection is deliberately still not
implemented: existing account lockout and per-address sign-in limits already
cover password guessing.
