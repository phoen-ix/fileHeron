"""Append-only server-error log (v1.53.0).

One row per captured error event - HTTP 5xx (the exception handlers in
``middleware/errors.py``), opted-in HTTP 4xx, and failed scheduled tasks
(``services/cron_tracker.py``). The ``notify_admin_error`` worker writes the row
*before* the alert saferails, so the log is complete even when the matching email
was deduped, hourly-capped, or the alert feature is off entirely - logging and
alerting are decoupled (``services/error_log.py`` logs; ``services/error_alert.py``
alerts).

Context only: client IP, exception type, masked-free message (<=500 chars),
method/path, status/code, request_id, acting user id - never a traceback, request
body, or query string (the path already drops the query string upstream). The IP
is proxy-resolved and is the key field for spotting scans; see the note on the
column for the one case where it is a bridge address rather than a client.
``alerted`` records whether an email actually went out for this row. Bounded by the
``error_log`` retention window in ``workers/prune_history.py``.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..utils.timeutil import utc_now

# BigInteger PK doesn't autoincrement on SQLite; INTEGER there (tests).
_BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class ErrorLog(Base):
    __tablename__ = "error_log"
    __table_args__ = (
        # Filter by code, ordered by time (leftmost prefix also serves code-only).
        Index("ix_error_log_code_created", "code", "created_at"),
        # "show everything from this IP" (scan triage), newest first.
        Index("ix_error_log_ip_created", "ip", "created_at"),
        # Group every occurrence of one error shape.
        Index("ix_error_log_signature", "signature"),
    )

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, index=True
    )

    # "http" | "worker"
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    exception_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    method: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Request path (query string already stripped upstream) or the cron job name.
    path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    job_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # The client address AS RESOLVED AT THE BACKEND (proxy-resolved). String(45)
    # fits IPv6; mirrors login_attempts.
    #
    # Usually the real client IP, but NOT always, and the difference is visible
    # in the admin viewer: a request that reaches the frontend nginx without an
    # X-Forwarded-For - i.e. straight to the loopback-published port rather than
    # through Traefik - has no forwarded address to recover, so what lands here
    # is nginx's own peer, the docker bridge gateway. Measured: 13 such rows on
    # the reference instance (12 scanner-bait 404s from a host-local curl, plus
    # one AUTH_REQUIRED), against 2,239 total.
    #
    # This is not a defect and must not be "fixed" by blanking it: the value is
    # what the backend actually saw, and both published ports bind 127.0.0.1 so
    # such rows can only be host-local by construction. Nor does it cause
    # mis-blocking - `utils/client_ip.is_blockable` refuses every non-global
    # address, deliberately, because a wide block containing the bridge would
    # take the SPA, tusd, the healthcheck and the updater down with it.
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # No FK: forensic, must survive user deletion / erasure.
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auth_via: Mapped[str | None] = mapped_column(String(16), nullable=True)

    signature: Mapped[str] = mapped_column(String(16), nullable=False)
    # True once an alert email was actually sent for this row.
    alerted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
