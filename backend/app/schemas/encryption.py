"""Admin: encryption at rest of stored files (/api/admin/settings/encryption)."""
from __future__ import annotations

from pydantic import Field

from .common import APIBaseModel


class EncryptionFileCounts(APIBaseModel):
    encrypted: int
    plaintext: int
    plaintext_bytes: int
    awaiting_encryption: int


class EncryptionAttachmentCounts(APIBaseModel):
    encrypted: int
    plaintext: int


class EncryptionLastRun(APIBaseModel):
    finished_at: str
    encrypted: int
    failed: int
    deferred: int
    skipped: int
    remaining: int
    # "budget" (the run's time ran out), "insufficient_space", "disabled" (the
    # switch went off mid-run), or None.
    stopped: str | None = None


class EncryptionSettingsResponse(APIBaseModel):
    enabled: bool
    backend: str
    files: EncryptionFileCounts
    inbound_attachments: EncryptionAttachmentCounts
    pending_purges: int
    failed_purges: int
    last_run: EncryptionLastRun | None = None
    # Objects the backfill gave up on for a day after repeated failures; None
    # when Redis cannot say.
    deferred: int | None = None
    backfill_task_enabled: bool


class UpdateEncryptionSettingsRequest(APIBaseModel):
    enabled: bool
    # Turning encryption ON commits the instance's data recovery to the
    # JWT_SECRET in .env, which backups deliberately do not contain. The admin
    # has to say they know - enforced here, not only by the page's checkbox.
    acknowledge_key_custody: bool = False
    password: str | None = Field(default=None, max_length=256)
