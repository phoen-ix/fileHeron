"""Release scan-guard blocks from the host. The escape hatch.

    docker compose exec backend python scripts/unblock_ip.py <ip|cidr>
    docker compose exec backend python scripts/unblock_ip.py --all
    docker compose exec backend python scripts/unblock_ip.py --list

Exists because the block check in `middleware/scan_guard.py` runs BEFORE routing
and before auth: an admin caught by a block - their own office address, a
mistuned threshold, a network escalation that reached them - cannot load the
admin page to undo it, because that request is refused too. Without this the only
recovery is DB surgery, which is not a recovery procedure.

Runnable BOTH ways, deliberately:

    python scripts/unblock_ip.py ...
    python -m scripts.unblock_ip ...

`scripts/promote_user.py` documents why: its own advertised invocation did not
work for four releases, and that was discovered by someone who was already locked
out. A recovery tool that fails at the moment of need is worse than none.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # plain `python scripts/unblock_ip.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ipaddress  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.ip_block import IpBlock  # noqa: E402
from app.services import scan_guard as guard_svc  # noqa: E402
from app.utils.timeutil import utc_now  # noqa: E402


def _live_matches(db, live, subject: str) -> list[IpBlock]:
    """Live blocks affecting ``subject``, matched by CONTAINMENT, not by string.

    The tool used to compare `IpBlock.subject == subject`, which fails in
    exactly the situation it exists for: an admin locked out by a /24 network
    escalation types their own address, is told "no live block for 1.2.3.4",
    and stays locked out. It also missed pure notation differences -
    `45.148.10.5/24` against a stored `45.148.10.0/24`, or any IPv6 written
    with different compression or case.

    Naming a CIDR still releases that CIDR exactly; naming an address releases
    the address row AND every live network row containing it.
    """
    text = subject.strip()
    try:
        net = ipaddress.ip_network(text, strict=False)
    except ValueError:
        # Not parseable as either - fall back to the literal comparison so a
        # malformed argument fails loudly rather than matching everything.
        return live.filter(IpBlock.subject == text).all()

    if net.num_addresses > 1:
        canonical = str(net)
        return [r for r in live.all() if _same_network(r.subject, canonical)]
    # `blocks_covering` walks history as well, so intersect with the live set -
    # releasing an already-expired row would print a reassuring line about
    # something that was not blocking anyone.
    covering = {r.id for r in guard_svc.blocks_covering(db, str(net.network_address))}
    return [r for r in live.all() if r.id in covering]


def _same_network(stored: str, canonical: str) -> bool:
    try:
        return str(ipaddress.ip_network(stored, strict=False)) == canonical
    except ValueError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Release scan-guard IP blocks.")
    ap.add_argument("subject", nargs="?", help="address or CIDR to release")
    ap.add_argument("--all", action="store_true", help="release every live block")
    ap.add_argument("--list", action="store_true", help="show live blocks and exit")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        live = db.query(IpBlock).filter(
            IpBlock.released_at.is_(None), IpBlock.expires_at > utc_now()
        )
        if args.list:
            rows = live.all()
            if not rows:
                print("no live blocks")
            for r in rows:
                kind = "network" if r.is_network else "address"
                print(f"  {r.subject:<44} {kind:<8} {r.reason:<14} expires {r.expires_at}")
            return 0

        if not args.all and not args.subject:
            ap.error("give an address/CIDR, or --all, or --list")

        rows = live.all() if args.all else _live_matches(db, live, args.subject)
        if not rows:
            print("nothing to release" if args.all else f"no live block for {args.subject}")
            return 1
        released = 0
        for r in rows:
            # Through the service, not a bare UPDATE: it stamps released_by_id,
            # writes the `ip_block_released` audit row marked `via: host-cli`,
            # and clears the Redis counters that produced the block. Setting
            # `released_at` by hand left a host-side release invisible in the
            # audit log and re-blockable by the next request off a counter still
            # sitting at the threshold.
            if guard_svc.release(db, block_id=r.id, actor_id=None, via="host-cli"):
                released += 1
                print(f"released {r.subject}")
        db.commit()
        # The running API process caches the blocklist for up to _CACHE_TTL_SEC,
        # and this is a SEPARATE process - so say plainly when it takes effect
        # rather than letting someone think the tool failed.
        from app.services.scan_guard import _CACHE_TTL_SEC

        print(f"{released} released; effective within {int(_CACHE_TTL_SEC)}s")
        return 0 if released else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
