"""Temporary source blocks applied by the scan guard (v2.10.0).

One row per blocked subject - either a single address (`203.0.113.7`) or, when
network escalation is switched on, the containing network (`195.178.110.0/24`).
A repeat offence EXTENDS the live row rather than inserting a second one, so the
table stays small and the admin list reads as history: expired rows are the
record of what happened, the single unexpired row is what is in force.

This table is the source of truth. The request hot path never reads it - the
guard serves from a process cache (see `services/scan_guard.py`) because
`redis_client` sets `socket_timeout=2`, so a per-request round trip would add two
seconds to EVERY request during a Redis slowdown. The DB is what makes a block
survive a restart and a Redis flush both.

`Integer` PK, not BigInteger: this is a low-volume table by construction (93
distinct sources in two months on the reference instance) and
`scan_guard.max_new_blocks_per_min` caps insert rate. CLAUDE.md reserves
BigInteger for the genuinely high-volume logs.

Rows are pseudonymous security records, not user data: `released_by_id` carries
no FK (forensic, mirrors `error_log.user_id`), and erasure deliberately does NOT
match them by IP - an IP does not identify a person on shared NAT, and matching
would delete a third party's record. Bounded instead by the `ip_block` retention
window in `workers/prune_history.py`.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.timeutil import utc_now


class IpBlock(Base):
    __tablename__ = "ip_blocks"
    __table_args__ = (
        # Cache refresh: "every block still in force", newest first.
        Index("ix_ip_blocks_expires_at", "expires_at"),
        # Strike count for one subject inside the lookback window.
        Index("ix_ip_blocks_subject_created", "subject", "created_at"),
        # Distinct blocked IPs per network - the escalation evidence query.
        Index("ix_ip_blocks_network_created", "network", "created_at"),
        # List ordering + retention prune.
        Index("ix_ip_blocks_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # The thing that is blocked: a bare address, or a CIDR when is_network.
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    # The /24 (IPv4) or /64 (IPv6) CONTAINING `subject`, set on every row so the
    # escalation query is one indexed count rather than a scan plus parsing.
    # Computed with `ipaddress`, never string surgery - `utils/geohash.py`
    # documents why splitting a textual IPv6 mishandles `::`.
    network: Mapped[str] = mapped_column(String(64), nullable=False)
    is_network: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    # Signal name ("probe_path" | "api_404" | "auth_failure"), "network", or "manual".
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    # "auto" | "manual" - a manual block must never be extended by the escalation
    # ladder or swept by the strike counter.
    source: Mapped[str] = mapped_column(
        String(8), nullable=False, default="auto", server_default="auto"
    )

    # Offending requests that produced or extended this block.
    hit_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Nth block for this subject inside the lookback - drives the duration ladder
    # and lets the admin page say "3rd offence" without a second query.
    strikes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    # One REDACTED sample path (middleware.errors._redact_path), so the evidence an
    # admin needs is present and a live public-link or reset token never is.
    last_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)

    # Set when an admin releases early; the row is kept as history.
    released_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    released_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
