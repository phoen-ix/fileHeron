"""Daily cron: bound the unbounded forensic tables.

`audit_log`, `download_log`, `login_attempts` all use BigInteger PKs
with no retention. On a multi-year deployment they grow without
bound and slow down filtered queries (admin audit-log view, etc.).

This cron does batched DELETEs (10k rows per batch, with a small
sleep between batches to spread the DB load) on rows older than
the per-table retention window. All windows are env-configurable;
set any to 0 to disable that table's pruning.

Note: audit_log retention has compliance implications in regulated
environments. The 365d default is conservative; operators in
PCI/HIPAA/etc. contexts should bump via env.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models.audit_log import AuditLog
from ..models.download_log import DownloadLog
from ..models.email_log import EmailLog
from ..models.login_attempt import LoginAttempt
from ..models.webhook import WebhookDelivery
from ..services.cron_tracker import track_cron
from ..utils.timeutil import utc_now

logger = logging.getLogger("fileheron.workers.prune_history")

_BATCH_SIZE = 10_000
_INTER_BATCH_SLEEP_SEC = 5.0




async def _prune_table(
    table_name: str, days: int, age_column, model
) -> int:
    """DELETE FROM <table> WHERE age_column < cutoff, in batches.

    SQLite doesn't support DELETE … LIMIT, but the test path uses
    SQLite. Fall back to delete-all-matching when LIMIT is unsupported.
    """
    if days <= 0:
        return 0
    cutoff = utc_now() - timedelta(days=days)
    total = 0
    while True:
        db: Session = SessionLocal()
        try:
            ids = [
                pk
                for (pk,) in db.query(model.id)
                .filter(age_column < cutoff)
                .limit(_BATCH_SIZE)
                .all()
            ]
            if not ids:
                break
            result = db.execute(delete(model).where(model.id.in_(ids)))
            db.commit()
            total += result.rowcount or len(ids)
            if len(ids) < _BATCH_SIZE:
                break
        finally:
            db.close()
        # Yield + brief sleep so we don't monopolize the DB.
        await asyncio.sleep(_INTER_BATCH_SLEEP_SEC)
    if total:
        logger.info("prune_history: %s pruned=%d (older than %dd)", table_name, total, days)
    return total


@track_cron("prune_history")
async def prune_history(_ctx) -> dict:
    # Resolve the admin-tunable retention windows once (kv overlay, env default).
    from ..services import settings_registry as _sr
    _db0 = SessionLocal()
    try:
        audit_days = _sr.effective(_db0, _sr.K.AUDIT_LOG_RETENTION_DAYS)
        download_days = _sr.effective(_db0, _sr.K.DOWNLOAD_LOG_RETENTION_DAYS)
        email_days = _sr.effective(_db0, _sr.K.EMAIL_LOG_RETENTION_DAYS)
        login_days = _sr.effective(_db0, _sr.K.LOGIN_ATTEMPT_RETENTION_DAYS)
        webhook_days = _sr.effective(_db0, _sr.K.WEBHOOK_DELIVERY_RETENTION_DAYS)
        inbound_days = _sr.effective(_db0, _sr.K.IMAP_MESSAGE_RETENTION_DAYS)
    finally:
        _db0.close()
    audit_pruned = await _prune_table(
        "audit_log", audit_days, AuditLog.created_at, AuditLog
    )
    download_pruned = await _prune_table(
        "download_log", download_days, DownloadLog.accessed_at, DownloadLog
    )
    email_pruned = await _prune_table(
        "email_log", email_days, EmailLog.created_at, EmailLog
    )
    login_pruned = await _prune_table(
        "login_attempts",
        login_days,
        LoginAttempt.attempted_at,
        LoginAttempt,
    )
    webhook_pruned = await _prune_table(
        "webhook_deliveries", webhook_days, WebhookDelivery.created_at, WebhookDelivery
    )
    inbound_pruned = await _prune_inbound(inbound_days)
    return {
        "audit_log": audit_pruned,
        "download_log": download_pruned,
        "email_log": email_pruned,
        "login_attempts": login_pruned,
        "webhook_deliveries": webhook_pruned,
        "inbound_messages": inbound_pruned,
    }


async def _prune_inbound(days: int) -> int:
    """Prune inbound messages older than ``days`` — deleting their attachment
    blobs from the storage backend first (the DB cascade only removes rows)."""
    if days <= 0:
        return 0
    from ..models.inbound_attachment import InboundAttachment
    from ..models.inbound_message import InboundMessage
    from ..services import storage_backend as storage_svc

    cutoff = utc_now() - timedelta(days=days)
    db: Session = SessionLocal()
    try:
        keys = [
            k
            for (k,) in db.query(InboundAttachment.storage_key)
            .join(InboundMessage, InboundMessage.id == InboundAttachment.message_id)
            .filter(InboundMessage.created_at < cutoff)
            .all()
        ]
    finally:
        db.close()
    if keys:
        backend = storage_svc.get_storage_backend()
        for k in keys:
            try:
                backend.delete(k)
            except Exception:
                logger.warning("prune_history: failed to delete inbound blob %s", k)
    return await _prune_table(
        "inbound_messages", days, InboundMessage.created_at, InboundMessage
    )
