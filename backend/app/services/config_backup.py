"""Configuration backup / restore (v1.33.0).

Disaster-recovery for an instance's *configuration* (not its shared files - those
are short-lived and deliberately excluded). An admin exports a category-selectable
backup and imports it to rebuild a crashed system.

Three moving parts:
  - ``build_backup``  - serialise the chosen categories into a versioned JSON
    container (see the format below).
  - ``parse_backup``  - validate magic + version + (passphrase) decrypt.
  - ``preview_backup`` / ``apply_backup`` - dry-run summary, then the FK-safe
    REPLACE import. Import invalidates ALL active shares (config restore changes
    the world out from under any live share) and revokes all sessions.

Backup file (``*.fhbackup.json``): the OUTER envelope is always plaintext JSON so
import can detect the secret mode without a passphrase; the payload is inline
(plaintext / ciphertext modes) or a passphrase-encrypted blob.

    {
      "magic": "FILEHERON_CONFIG_BACKUP", "format_version": 1,
      "created_at": "...", "app_version": "...", "git_sha": "...",
      "alembic_revision": "...", "secret_mode": "passphrase|ciphertext|exclude",
      "categories": [...], "include_env": false, "warnings": [...],
      "encryption": {"kdf":"scrypt","n":...,"r":...,"p":...,"salt":"<b64>"},   # passphrase only
      "payload": {...}                  # inline (plaintext/ciphertext)
      # OR "payload_encrypted": "<fernet token>"   # passphrase
    }

Secret columns (app_settings encrypted values, oidc client_secret, webhook
secret, totp secret) carry one of three shapes inside the payload depending on
secret_mode: ``{"__plain__": "..."}`` (decrypted, re-encrypted under the target's
JWT_SECRET on import), ``{"__cipher__": "..."}`` / ``{"__cipher_b64__": "..."}``
(raw blob, only decrypts if the target reuses the same JWT_SECRET), or absent
(excluded).
"""
from __future__ import annotations

import base64
import enum
import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from cryptography.fernet import InvalidToken
from sqlalchemy import func, or_
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from ..config import settings as cfg
from ..middleware.errors import AppError
from ..models.app_setting import AppSetting
from ..models.audit_log import AuditEventType, AuditLog
from ..models.client_employee_connection import (
    ClientEmployeeConnection,
    ConnectionSource,
)
from ..models.download_log import DownloadLog
from ..models.email_log import EmailLog
from ..models.email_template_override import EmailTemplateOverride
from ..models.file import File, FileState
from ..models.group import Group
from ..models.group_member import GroupMember
from ..models.login_attempt import LoginAttempt
from ..models.notification import Notification, NotificationCategory
from ..models.oidc_provider import OIDCProvider
from ..models.share import Share, ShareState
from ..models.user import User, UserRole
from ..models.user_notification_preference import (
    NotificationChannel,
    UserNotificationPreference,
)
from ..models.user_recovery_code import UserRecoveryCode
from ..models.user_totp import UserTOTP
from ..models.user_webauthn_credential import UserWebAuthnCredential
from ..models.webhook import Webhook
from ..utils import crypto
from ..utils.crypto import normalize_email
from ..utils.timeutil import utc_now
from ..version import GIT_SHA, VERSION
from . import file as file_svc
from . import settings as settings_svc
from . import share as share_svc
from .audit import record_audit_event
from .connection import recompute_shared_group_connections_for_user
from .erasure import erase_user

logger = logging.getLogger("fileheron.config_backup")

MAGIC = "FILEHERON_CONFIG_BACKUP"
FORMAT_VERSION = 1

CATEGORIES = ("settings_branding", "oidc_webhooks", "groups", "users", "logs")
SECRET_MODES = ("passphrase", "ciphertext", "exclude")

# Runtime / cron state - looks like config but isn't; never exported.
_TRANSIENT_SETTING_KEYS = {
    settings_svc.Keys.IMAP_LAST_POLL_AT,
    settings_svc.Keys.IMAP_LAST_SUCCESS_AT,
    settings_svc.Keys.IMAP_LAST_UID,
    settings_svc.Keys.IMAP_UIDVALIDITY,
    settings_svc.Keys.STORAGE_CRITICAL_LOW,
    # Runtime drain state - exporting it would re-arm maintenance mode (and a
    # past-deadline pending update that drain_pending_update fires at once) on
    # every restore, so keep it out of the portable config backup.
    settings_svc.Keys.MAINTENANCE_ENABLED,
    settings_svc.Keys.MAINTENANCE_PENDING_UPDATE,
}
# Logo locators are system-specific (absolute paths / object keys); the bytes
# travel in the branding_logo section and locators are regenerated on import.
_LOGO_LOCATOR_KEYS = {
    settings_svc.Keys.BRANDING_LOGO_LOCATOR,
    settings_svc.Keys.BRANDING_LOGO_PNG_LOCATOR,
}
# app_settings whose JSON value embeds user / group IDs that must be remapped
# through the identity import.
_JSON_USER_ID_KEYS = {
    settings_svc.Keys.API_TOKEN_ALLOWED_USERS,
    settings_svc.Keys.PUBLIC_LINK_ALLOWED_USERS,
    settings_svc.Keys.SHARE_APPROVAL_APPROVER_USERS,
}
_JSON_GROUP_ID_KEYS = {
    settings_svc.Keys.API_TOKEN_ALLOWED_GROUPS,
    settings_svc.Keys.PUBLIC_LINK_ALLOWED_GROUPS,
    settings_svc.Keys.TWOFA_REQUIRED_GROUPS,
    settings_svc.Keys.SHARE_APPROVAL_APPROVER_GROUPS,
}
# os.environ keys captured into the optional env snapshot. Hardcoded whitelist -
# we NEVER dump **os.environ.
_ENV_WHITELIST = (
    "JWT_SECRET", "DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME",
    "TUS_HOOK_SECRET", "APP_URL", "REDIS_HOST", "REDIS_PORT",
    "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
    "SMTP_FROM_EMAIL", "SMTP_FROM_NAME",
    "STORAGE_BACKEND", "STORAGE_ROOT",
    "S3_ENDPOINT_URL", "S3_BUCKET", "S3_REGION",
    "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_KEY_PREFIX",
)


# ---------------------------------------------------------------------------
# Generic column (de)serialisation
# ---------------------------------------------------------------------------

def _columns(model):
    """The mapped columns, keyed by ORM ATTRIBUTE name.

    `sa_inspect(model).columns` is the *table* collection, keyed by DB column
    name - and the two diverge wherever a model renames a column. `AuditLog`
    does exactly that (`extra` -> `metadata_json`), and both sides of this
    module speak ORM names: `_row_to_dict` uses getattr, `_build` passes
    kwargs to the constructor. So exporting the `logs` category raised
    AttributeError on the first audit row - which every real instance has,
    making that whole category unusable - and importing one would have raised
    TypeError. Found while testing the audit-preservation fix, 2026-07-30."""
    mapper = sa_inspect(model).mapper
    return [
        SimpleNamespace(key=attr.key, name=attr.expression.key, type=attr.expression.type)
        for attr in mapper.column_attrs
    ]


def _enc(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bytes):
        return {"__b64__": base64.b64encode(v).decode("ascii")}
    if isinstance(v, datetime):
        return {"__dt__": v.isoformat()}
    if isinstance(v, enum.Enum):
        return v.value
    return v


def _dec(v: Any, coltype: Any = None) -> Any:
    if v is None:
        return None
    if isinstance(v, dict):
        if "__b64__" in v:
            return base64.b64decode(v["__b64__"])
        if "__dt__" in v:
            return datetime.fromisoformat(v["__dt__"])
    if coltype is not None:
        ec = getattr(coltype, "enum_class", None)
        if ec is not None and isinstance(v, str):
            return ec(v)
    return v


def _dt(v: Any) -> Any:
    """Decode a datetime field on a hand-built row (no coltype context)."""
    if isinstance(v, dict) and "__dt__" in v:
        return datetime.fromisoformat(v["__dt__"])
    return v


def _row_to_dict(obj, *, drop: frozenset[str] = frozenset()) -> dict:
    out: dict[str, Any] = {}
    for col in _columns(type(obj)):
        if col.key in drop:
            continue
        out[col.key] = _enc(getattr(obj, col.key))
    return out


def _build(model, data: dict, *, overrides: dict | None = None, skip: frozenset[str] = frozenset()):
    cols = {c.key: c for c in _columns(model)}
    # Accept a raw DB column name too, so a payload written before _columns
    # started speaking ORM names still loads.
    aliases = {c.name: c.key for c in _columns(model) if c.name != c.key}
    kwargs: dict[str, Any] = {}
    for k, v in data.items():
        if k in skip:
            continue
        k = aliases.get(k, k)
        col = cols.get(k)
        if col is None:
            continue
        kwargs[k] = _dec(v, col.type)
    if overrides:
        kwargs.update(overrides)
    return model(**kwargs)


# ---------------------------------------------------------------------------
# Secret transforms
# ---------------------------------------------------------------------------

def _emit_secret_str(stored: str | None, *, mode: str, warnings: list[str], label: str):
    """app_settings encrypted value / oidc client_secret / webhook secret -
    all stored as a Fernet ASCII string (or ``""`` when unset)."""
    if not stored:
        return None
    if mode == "exclude":
        return None
    if mode == "ciphertext":
        return {"__cipher__": stored}
    try:
        return {"__plain__": crypto.decrypt_setting(stored)}
    except Exception:
        warnings.append(f"could not decrypt {label}; exported without it")
        return None


def _emit_secret_totp(stored: bytes | None, *, mode: str, warnings: list[str], label: str):
    if not stored:
        return None
    if mode == "exclude":
        return None
    if mode == "ciphertext":
        return {"__cipher_b64__": base64.b64encode(stored).decode("ascii")}
    try:
        return {"__plain__": crypto.decrypt_totp_secret(stored)}
    except Exception:
        warnings.append(f"could not decrypt {label}; exported without it")
        return None


def _ingest_secret_str(val: Any) -> str | None:
    """-> stored Fernet ASCII string (re-encrypted under the target JWT_SECRET
    for ``__plain__``, verbatim for ``__cipher__``), or None to leave unset."""
    if not isinstance(val, dict):
        return None
    if "__plain__" in val:
        return crypto.encrypt_setting(val["__plain__"])
    if "__cipher__" in val:
        return val["__cipher__"]
    return None


def _ingest_secret_totp(val: Any) -> bytes | None:
    if not isinstance(val, dict):
        return None
    if "__plain__" in val:
        return crypto.encrypt_totp_secret(val["__plain__"])
    if "__cipher_b64__" in val:
        return base64.b64decode(val["__cipher_b64__"])
    return None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def capture_env_snapshot() -> dict[str, str]:
    return {k: os.environ[k] for k in _ENV_WHITELIST if k in os.environ}


def _current_alembic_revision(db: Session) -> str | None:
    try:
        from alembic.runtime.migration import MigrationContext

        return MigrationContext.from_connection(db.connection()).get_current_revision()
    except Exception:
        return None


def _export_logo(db: Session, warnings: list[str]) -> dict:
    loc = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_LOCATOR)
    if not loc:
        return {"present": False}
    from .storage_backend import get_storage_backend

    backend = get_storage_backend()
    out: dict[str, Any] = {"present": True}
    try:
        with backend.open(loc) as fh:
            out["original_b64"] = base64.b64encode(fh.read()).decode("ascii")
    except Exception:
        warnings.append("branding logo bytes unreadable; skipped")
        return {"present": False}
    png = settings_svc.get(db, settings_svc.Keys.BRANDING_LOGO_PNG_LOCATOR)
    if png:
        try:
            with backend.open(png) as fh:
                out["png_b64"] = base64.b64encode(fh.read()).decode("ascii")
        except Exception:
            warnings.append("branding logo PNG rendition unreadable; skipped")
    return out


def export_settings_branding(db: Session, *, mode: str, warnings: list[str]) -> dict:
    rows: list[dict] = []
    for s in db.query(AppSetting).all():
        if s.key in _TRANSIENT_SETTING_KEYS or s.key in _LOGO_LOCATOR_KEYS:
            continue
        if s.is_encrypted:
            rows.append({
                "key": s.key,
                "is_encrypted": True,
                "secret": _emit_secret_str(
                    s.value, mode=mode, warnings=warnings, label=f"setting {s.key}"
                ),
            })
        else:
            rows.append({"key": s.key, "is_encrypted": False, "value": s.value})
    overrides = [
        _row_to_dict(o, drop=frozenset({"id", "updated_by_id"}))
        for o in db.query(EmailTemplateOverride).all()
    ]
    return {
        "app_settings": rows,
        "branding_logo": _export_logo(db, warnings),
        "email_template_overrides": overrides,
    }


def export_oidc_webhooks(db: Session, *, mode: str, warnings: list[str]) -> dict:
    providers = []
    for p in db.query(OIDCProvider).all():
        d = _row_to_dict(p, drop=frozenset(
            {"client_secret_encrypted", "created_by_id", "updated_by_id", "created_at", "updated_at"}
        ))
        d["client_secret_encrypted"] = _emit_secret_str(
            p.client_secret_encrypted, mode=mode, warnings=warnings,
            label=f"OIDC provider {p.name} secret",
        )
        providers.append(d)
    hooks = []
    for w in db.query(Webhook).all():
        d = _row_to_dict(w, drop=frozenset(
            {"id", "secret_encrypted", "created_by_id", "created_at", "updated_at"}
        ))
        d["secret_encrypted"] = _emit_secret_str(
            w.secret_encrypted, mode=mode, warnings=warnings,
            label=f"webhook {w.name} secret",
        )
        hooks.append(d)
    return {"oidc_providers": providers, "webhooks": hooks}


def export_groups(db: Session) -> dict:
    groups = [
        {
            "id": g.id, "name": g.name, "name_normalized": g.name_normalized,
            "description": g.description, "is_company_inbox": g.is_company_inbox,
            "created_by_id": g.created_by_id,
        }
        for g in db.query(Group).all()
    ]
    members = [
        {"group_id": m.group_id, "user_id": m.user_id, "joined_at": _enc(m.joined_at)}
        for m in db.query(GroupMember).all()
    ]
    return {"groups": groups, "group_members": members}


def export_users(db: Session, *, mode: str, warnings: list[str]) -> dict:
    users = [
        _row_to_dict(u, drop=frozenset(
            {"failed_login_count", "locked_until", "lockout_email_sent_at"}
        ))
        for u in db.query(User).all()
    ]
    totp = [
        {
            "user_id": t.user_id,
            "secret": _emit_secret_totp(
                t.secret_encrypted, mode=mode, warnings=warnings,
                label=f"TOTP secret for user {t.user_id}",
            ),
            "enabled_at": _enc(t.enabled_at),
            "last_used_counter": t.last_used_counter,
        }
        for t in db.query(UserTOTP).all()
    ]
    recovery = [
        {
            "user_id": r.user_id, "code_hash": r.code_hash,
            "created_at": _enc(r.created_at), "used_at": _enc(r.used_at),
        }
        for r in db.query(UserRecoveryCode).all()
    ]
    webauthn = [
        _row_to_dict(c, drop=frozenset({"id"}))
        for c in db.query(UserWebAuthnCredential).all()
    ]
    prefs = [
        {"user_id": p.user_id, "category": p.category.value, "channel": p.channel.value}
        for p in db.query(UserNotificationPreference).all()
    ]
    # invite-source connections are sticky and not derivable from group
    # membership, so they must travel in the backup; shared_group rows are
    # recomputed on import instead.
    connections = [
        {
            "client_user_id": c.client_user_id,
            "employee_user_id": c.employee_user_id,
            "created_at": _enc(c.created_at),
        }
        for c in db.query(ClientEmployeeConnection).filter(
            ClientEmployeeConnection.source == ConnectionSource.invite
        ).all()
    ]
    return {
        "users": users, "user_totp": totp, "user_recovery_codes": recovery,
        "user_webauthn_credentials": webauthn, "user_notification_preferences": prefs,
        "client_employee_connections": connections,
    }


# The `logs` category is the only unbounded thing in a backup: a year of
# retention is hundreds of thousands of rows, email_log carries whole message
# bodies, and the export holds four copies at once (ORM rows, dicts, the JSON
# string, the encrypted blob). That OOM-killed the container of the very
# instance an admin was taking a disaster-recovery backup from. Truncating
# costs nothing real: import refuses any file over 50 MB, so an untruncated log
# export could not have been restored anyway.
_MAX_EXPORT_LOG_ROWS = 25_000


def export_logs(db: Session, *, warnings: list[str]) -> dict:
    from sqlalchemy.orm import undefer

    def _dump(model, *, options=()):
        rows = (
            db.query(model)
            .options(*options)
            .order_by(model.id.desc())
            .limit(_MAX_EXPORT_LOG_ROWS + 1)
            .all()
        )
        if len(rows) > _MAX_EXPORT_LOG_ROWS:
            rows = rows[:_MAX_EXPORT_LOG_ROWS]
            warnings.append(
                f"{model.__tablename__}: exported only the newest "
                f"{_MAX_EXPORT_LOG_ROWS} rows"
            )
        return [_row_to_dict(r, drop=frozenset({"id"})) for r in reversed(rows)]

    return {
        "audit_log": _dump(AuditLog),
        # body_text / body_html are deferred on the model, so _row_to_dict's
        # getattr over every column fired two extra SELECTs per row and pulled
        # the bodies in one at a time anyway. Load them with the row.
        "email_log": _dump(
            EmailLog,
            options=(undefer(EmailLog.body_text), undefer(EmailLog.body_html)),
        ),
        "download_log": _dump(DownloadLog),
        "login_attempts": _dump(LoginAttempt),
        "notifications": _dump(Notification),
    }


def build_backup(
    db: Session,
    *,
    categories: list[str],
    secret_mode: str,
    passphrase: str | None,
    include_env: bool,
) -> bytes:
    warnings: list[str] = []
    payload: dict[str, Any] = {}
    if "settings_branding" in categories:
        payload["settings_branding"] = export_settings_branding(db, mode=secret_mode, warnings=warnings)
    if "oidc_webhooks" in categories:
        payload["oidc_webhooks"] = export_oidc_webhooks(db, mode=secret_mode, warnings=warnings)
    if "groups" in categories:
        payload["groups"] = export_groups(db)
    if "users" in categories:
        payload["users"] = export_users(db, mode=secret_mode, warnings=warnings)
    if "logs" in categories:
        payload["logs"] = export_logs(db, warnings=warnings)
    if include_env:
        payload["env_snapshot"] = capture_env_snapshot()

    envelope: dict[str, Any] = {
        "magic": MAGIC,
        "format_version": FORMAT_VERSION,
        "created_at": utc_now().isoformat(),
        "app_version": VERSION,
        "git_sha": GIT_SHA,
        "alembic_revision": _current_alembic_revision(db),
        "secret_mode": secret_mode,
        "categories": list(categories),
        "include_env": include_env,
        "warnings": warnings,
    }
    if secret_mode == "passphrase":
        assert passphrase
        salt = crypto.new_backup_salt()
        envelope["encryption"] = {
            "kdf": "scrypt", "n": crypto.SCRYPT_N, "r": crypto.SCRYPT_R,
            "p": crypto.SCRYPT_P, "salt": base64.b64encode(salt).decode("ascii"),
            "version": 1,
        }
        envelope["payload_encrypted"] = crypto.encrypt_with_passphrase(
            json.dumps(payload).encode("utf-8"), passphrase, salt
        )
    else:
        envelope["payload"] = payload
    return json.dumps(envelope, indent=2).encode("utf-8")


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

@dataclass
class ParsedBackup:
    secret_mode: str
    categories: list[str]
    include_env: bool
    payload: dict
    app_version: str | None
    git_sha: str | None
    alembic_revision: str | None
    created_at: str | None
    warnings: list[str] = field(default_factory=list)


def parse_backup(raw: bytes, *, passphrase: str | None) -> ParsedBackup:
    try:
        env = json.loads(raw)
    except Exception as e:
        raise AppError(400, "BACKUP_CORRUPT", "File is not valid JSON.") from e
    if not isinstance(env, dict) or env.get("magic") != MAGIC:
        raise AppError(400, "BACKUP_CORRUPT", "Not a fileHeron configuration backup.")
    fmt = env.get("format_version")
    if not isinstance(fmt, int) or fmt > FORMAT_VERSION:
        raise AppError(
            409, "BACKUP_VERSION_INCOMPATIBLE",
            f"Backup format v{fmt} is newer than this system supports (v{FORMAT_VERSION}). "
            "Update fileHeron before importing.",
        )
    secret_mode = env.get("secret_mode")
    if secret_mode not in SECRET_MODES:
        raise AppError(400, "BACKUP_CORRUPT", "Unknown or missing secret mode.")

    if secret_mode == "passphrase":
        if not passphrase:
            raise AppError(
                400, "BACKUP_PASSPHRASE_REQUIRED",
                "This backup is passphrase-encrypted; a passphrase is required.",
            )
        enc = env.get("encryption") or {}
        try:
            salt = base64.b64decode(enc["salt"])
            token = env["payload_encrypted"]
        except (KeyError, ValueError, TypeError) as e:
            raise AppError(400, "BACKUP_CORRUPT", "Encrypted backup is malformed.") from e
        try:
            kdf_n = int(enc.get("n", crypto.SCRYPT_N))
            kdf_r = int(enc.get("r", crypto.SCRYPT_R))
            kdf_p = int(enc.get("p", crypto.SCRYPT_P))
        except (TypeError, ValueError) as e:
            # These three numbers come out of the cleartext envelope, so a
            # hand-edited or truncated file can put a string or a null where an
            # integer belongs. That is a corrupt backup and has to read as one,
            # not as an unhandled 500 out of int().
            raise AppError(
                400, "BACKUP_CORRUPT",
                "Backup declares unsupported key-derivation parameters.",
            ) from e
        try:
            raw_payload = crypto.decrypt_with_passphrase(
                token, passphrase, salt, n=kdf_n, r=kdf_r, p=kdf_p,
            )
        except crypto.ScryptParamsRejectedError as e:
            # A crafted envelope can ask for terabytes of scrypt memory; refuse
            # before the KDF runs rather than OOM the container.
            raise AppError(
                400, "BACKUP_CORRUPT",
                "Backup declares unsupported key-derivation parameters.",
            ) from e
        except InvalidToken as e:
            raise AppError(
                400, "BACKUP_BAD_PASSPHRASE", "Wrong passphrase, or the backup is corrupted."
            ) from e
        try:
            payload = json.loads(raw_payload)
        except Exception as e:
            raise AppError(400, "BACKUP_CORRUPT", "Decrypted payload is not valid JSON.") from e
    else:
        payload = env.get("payload")
        if not isinstance(payload, dict):
            raise AppError(400, "BACKUP_CORRUPT", "Backup is missing its payload.")

    return ParsedBackup(
        secret_mode=secret_mode,
        categories=list(env.get("categories") or []),
        include_env=bool(env.get("include_env")),
        payload=payload,
        app_version=env.get("app_version"),
        git_sha=env.get("git_sha"),
        alembic_revision=env.get("alembic_revision"),
        created_at=env.get("created_at"),
        warnings=list(env.get("warnings") or []),
    )


# ---------------------------------------------------------------------------
# Import summary
# ---------------------------------------------------------------------------

@dataclass
class ImportSummary:
    dry_run: bool
    secret_mode: str
    categories: list[str]
    shares_to_invalidate: int = 0
    files_deleted: int = 0
    counts: dict[str, Any] = field(default_factory=dict)
    purged_users: list[str] = field(default_factory=list)
    purged_groups: list[str] = field(default_factory=list)
    sessions_revoked: int = 0
    env_snapshot_present: bool = False
    env_dotenv: str | None = None
    version_warning: str | None = None
    warnings: list[str] = field(default_factory=list)
    # What the import INSTALLS, named rather than counted - see the dry-run
    # builder. An admin approving a restore has to be able to see the identities
    # and the outbound endpoints it brings with it (audit #2).
    admins_installed: list[str] = field(default_factory=list)
    oidc_issuers: list[str] = field(default_factory=list)
    webhook_urls: list[str] = field(default_factory=list)


def _version_warning(db: Session, parsed: ParsedBackup) -> str | None:
    cur = _current_alembic_revision(db)
    bak = parsed.alembic_revision
    if bak and cur and bak != cur:
        return (
            f"Backup was taken at schema revision {bak}; this system is at {cur}. "
            "Import will proceed - review for configuration drift afterwards."
        )
    return None


def _render_dotenv(snapshot: dict[str, str]) -> str:
    return "\n".join(f"{k}={v}" for k, v in snapshot.items())


# ---------------------------------------------------------------------------
# Preview (dry run - no writes)
# ---------------------------------------------------------------------------

def preview_backup(db: Session, parsed: ParsedBackup) -> ImportSummary:
    p = parsed.payload
    summary = ImportSummary(
        dry_run=True, secret_mode=parsed.secret_mode, categories=parsed.categories,
        version_warning=_version_warning(db, parsed), warnings=list(parsed.warnings),
    )
    summary.shares_to_invalidate = (
        db.query(Share).filter(Share.state == ShareState.active).count()
    )
    if "settings_branding" in p:
        sb = p["settings_branding"]
        summary.counts["app_settings"] = len(sb.get("app_settings", []))
        summary.counts["branding_logo"] = bool(sb.get("branding_logo", {}).get("present"))
    if "oidc_webhooks" in p:
        ow = p["oidc_webhooks"]
        summary.counts["oidc_providers"] = len(ow.get("oidc_providers", []))
        summary.counts["webhooks"] = len(ow.get("webhooks", []))
    if "groups" in p:
        backup_nn = {g["name_normalized"] for g in p["groups"].get("groups", [])}
        summary.counts["groups"] = len(backup_nn)
        summary.counts["group_members"] = len(p["groups"].get("group_members", []))
        summary.purged_groups = sorted(
            g.name for g in db.query(Group).all() if g.name_normalized not in backup_nn
        )
    if "users" in p:
        backup_emails = {normalize_email(u["email"]) for u in p["users"].get("users", [])}
        existing_emails = {row[0] for row in db.query(User.email).all()}
        summary.counts["users_total"] = len(backup_emails)
        summary.counts["users_insert"] = len(backup_emails - existing_emails)
        summary.counts["users_update"] = len(backup_emails & existing_emails)
        summary.purged_users = sorted(existing_emails - backup_emails)
        # Name the ADMINS the import installs. "users_insert: 6" told an admin
        # nothing about what they were approving: a backup handed over "from the
        # old server" can carry an admin row with a known password hash, and the
        # preview had no way to show it (audit #2).
        summary.admins_installed = sorted(
            normalize_email(u["email"])
            for u in p["users"].get("users", [])
            if str(u.get("role", "")).endswith("admin")
        )
    ow = p.get("oidc_webhooks", {})
    if ow:
        # Same reasoning: an OIDC provider pointing at an attacker's IdP, or a
        # webhook shipping every share event to an external host, is a durable
        # grant that survives the admin rotating their own password.
        summary.oidc_issuers = sorted(
            str(d.get("issuer_url") or "") for d in ow.get("oidc_providers", [])
        )
        summary.webhook_urls = sorted(
            str(d.get("url") or "") for d in ow.get("webhooks", [])
        )
    if "logs" in p:
        summary.counts["logs"] = {k: len(v) for k, v in p["logs"].items()}
    if "env_snapshot" in p:
        summary.env_snapshot_present = True
        summary.env_dotenv = _render_dotenv(p["env_snapshot"])
    return summary


# ---------------------------------------------------------------------------
# Apply (REPLACE import)
# ---------------------------------------------------------------------------

def _remap_json_ids(val: str | None, idmap: dict[int, int]) -> str:
    if not val:
        return val or "[]"
    try:
        ids = json.loads(val)
    except Exception:
        return val
    if not isinstance(ids, list):
        return val
    return json.dumps([idmap[i] for i in ids if i in idmap])


def _resolve_log_user(bid, user_id_map, has_users, db, *, existing_ids):
    if bid is None:
        return None
    if has_users:
        return user_id_map.get(bid)
    return bid if bid in existing_ids else None


def _restore_log(
    db, model, rows, *, user_fk, user_id_map, has_users, required_user=False,
    fk_present_checks=(), null_fields=(),
):
    """Reload a log table. `fk_present_checks` = [(field, ref_model)] - a row whose
    non-null value isn't present in ref_model is SKIPPED (config backup excludes
    files/shares, so cross-system download_log rows reference rows that don't exist
    here and would raise an IntegrityError). `null_fields` are forced to None (stale
    self-refs like email_log.source_log_id that won't survive the id reassignment)."""
    existing_ids = {r[0] for r in db.query(User.id).all()}
    present: dict[str, set] = {
        field: {r[0] for r in db.query(ref_model.id).all()}
        for field, ref_model in fk_present_checks
    }
    db.query(model).delete(synchronize_session=False)
    db.flush()
    for d in rows:
        d = dict(d)
        if user_fk and user_fk in d:
            resolved = _resolve_log_user(
                d.get(user_fk), user_id_map, has_users, db, existing_ids=existing_ids
            )
            if required_user and resolved is None:
                continue
            d[user_fk] = resolved
        if any(
            d.get(field) is not None and d.get(field) not in present[field]
            for field, _ref in fk_present_checks
        ):
            continue
        for nf in null_fields:
            if nf in d:
                d[nf] = None
        db.add(_build(model, d, skip=frozenset({"id"})))
    db.flush()


def _preserved_audit_rows(db, *, since_id: int) -> list[dict]:
    """Audit rows a config import must NOT destroy, snapshotted before the
    audit_log wipe in step 7.

    Two classes. Everything written since the import began (``id > since_id``):
    the share-invalidation pass committed in step 1 and every erasure step 5
    performed - the import was erasing the record of its own destruction.
    And every ``user_erased`` row whatever its age: that is what the GDPR
    erasure receipt reads back, and restoring a config backup is not a licence
    to forget that somebody exercised their right (audit 2026-07-30)."""
    rows = (
        db.query(AuditLog)
        .filter(
            or_(
                AuditLog.id > since_id,
                AuditLog.event_type == AuditEventType.user_erased.value,
            )
        )
        .all()
    )
    return [_row_to_dict(r, drop=frozenset({"id"})) for r in rows]


def _reinsert_preserved_audit(db, rows: list[dict]) -> None:
    """Re-add the snapshot after the reload. Deliberately NOT routed through
    _restore_log: these ids are already local, and running them through the
    backup's old->new user map would re-point an actor at whoever happens to
    hold that id in the backup."""
    if not rows:
        return
    existing = {r[0] for r in db.query(User.id).all()}
    # Skip anything the restore has ALREADY put back. A backup that includes
    # `logs` carries the same `user_erased` rows this snapshot preserved, so
    # re-importing an instance's own backup wrote the erasure receipt twice -
    # and again on every later restore. A legal record that reads as two
    # separate erasures for one person is worse than no copy of it (audit #2).
    # `(event_type, target_id, created_at)` identifies an audit row; ids are not
    # comparable across the wipe.
    present = {
        (e, t, c.isoformat() if hasattr(c, "isoformat") else str(c))
        for (e, t, c) in db.query(
            AuditLog.event_type, AuditLog.target_id, AuditLog.created_at
        ).all()
    }
    for d in rows:
        d = dict(d)
        if d.get("actor_user_id") is not None and d["actor_user_id"] not in existing:
            d["actor_user_id"] = None
        created = d.get("created_at")
        key = (
            d.get("event_type"),
            d.get("target_id"),
            created.isoformat() if hasattr(created, "isoformat") else str(created),
        )
        if key in present:
            continue
        present.add(key)
        db.add(_build(AuditLog, d, skip=frozenset({"id"})))
    db.flush()


def _import_logo(db, logo, *, actor, warnings):
    if not logo or not logo.get("present"):
        return
    from .storage_backend import get_storage_backend

    backend = get_storage_backend()

    def _write(b64: str) -> str:
        data = base64.b64decode(b64)
        loc = backend.generate_locator(f"branding-logo-{uuid.uuid4().hex}")
        Path(cfg.TUS_UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=cfg.TUS_UPLOAD_DIR, suffix=".logo")
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
            backend.finalize(tmp, loc)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return loc

    try:
        if logo.get("original_b64"):
            settings_svc.set_value(
                db, key=settings_svc.Keys.BRANDING_LOGO_LOCATOR,
                value=_write(logo["original_b64"]), actor=actor,
            )
        if logo.get("png_b64"):
            settings_svc.set_value(
                db, key=settings_svc.Keys.BRANDING_LOGO_PNG_LOCATOR,
                value=_write(logo["png_b64"]), actor=actor,
            )
    except Exception:
        logger.exception("config import: branding logo restore failed")
        warnings.append("failed to restore branding logo bytes")


def _purge_user(db, user, *, actor, request) -> str:
    """Hard-delete where FKs allow (true purge); fall back to anonymise (erasure)
    when transactional references block the delete."""
    # Unlink the bytes of every file that would otherwise be cascade-deleted with
    # the user row (files they uploaded + files under their shares). MariaDB's FK
    # CASCADE drops those rows with NO storage-backend unlink, permanently leaking
    # the bytes (reclaim_orphaned_files can't see cascaded-away rows). hard_delete
    # is idempotent on already-deleted files.
    doomed = (
        db.query(File)
        .outerjoin(Share, File.share_id == Share.id)
        .filter(
            (File.uploaded_by_id == user.id) | (Share.created_by_id == user.id),
            File.state != FileState.deleted,
        )
        .all()
    )
    for f in doomed:
        try:
            file_svc.hard_delete(db, file=f, reason="user_purged", actor_user_id=actor.id)
        except Exception:
            logger.exception("config import: purge byte-unlink failed for file=%s", f.id)
    sp = db.begin_nested()
    try:
        db.delete(user)
        db.flush()
        sp.commit()
        return "deleted"
    except Exception:
        sp.rollback()
    # NOT here. `erase_user` both COMMITS (per file, deliberately - each unlink
    # is irreversible) and calls `db.rollback()` on failure, so running it
    # inside the import's transaction either commits the import early or
    # discards it wholesale. A savepoint does not contain either: SQLAlchemy's
    # `Session.rollback()` unwinds the whole transaction, savepoints included
    # (audit #2 cross-check found this in the savepoint that was supposed to be
    # the fix).
    #
    # Defer it. The caller runs the deferred erasures AFTER the import commits,
    # where erase_user's own transaction semantics are the only ones in play.
    return "deferred"


_USER_FIELD_SKIP = frozenset({"id", "email", "created_by_id", "oidc_provider_id"})


def _user_fields(d: dict) -> dict:
    cols = {c.key: c for c in _columns(User)}
    out: dict[str, Any] = {}
    for k, v in d.items():
        if k in _USER_FIELD_SKIP:
            continue
        col = cols.get(k)
        if col is None:
            continue
        out[k] = _dec(v, col.type)
    return out


def _validate_backup_payload(p: dict, actor: User) -> None:
    """Exercise the field coercions apply_backup performs, WITHOUT touching the
    DB, so a malformed-but-parseable backup is rejected with 400 BACKUP_CORRUPT
    BEFORE the irreversible active-share invalidation - instead of wiping every
    share and then 500ing mid-restore (audit M6). Covers the realistic failure
    modes: missing keys, bad role enum, bad notification category/channel, and
    secret/field decode errors on oidc/webhook/totp/webauthn rows."""
    try:
        users = p.get("users", {})
        for d in users.get("users", []):
            if "email" not in d:
                raise ValueError("a user row is missing 'email'")
            _user_fields(d)  # role enum + per-column decodes
        for pref in users.get("user_notification_preferences", []):
            _ = pref["user_id"]
            NotificationCategory(pref["category"])
            NotificationChannel(pref["channel"])
        for t in users.get("user_totp", []):
            _ = t["user_id"]
            _ingest_secret_totp(t.get("secret"))
        for r in users.get("user_recovery_codes", []):
            _ = (r["user_id"], r["code_hash"])
        # These three were missing, so a backup carrying any of them still
        # produced the wipe-then-500 this function exists to prevent: step 1
        # invalidates every active share and unlinks the files, committed, and
        # THEN step 3 raises KeyError 'client_user_id' (audit #2).
        for c in users.get("client_employee_connections", []):
            _ = (c["client_user_id"], c["employee_user_id"])
        for d in users.get("users", []):
            if not isinstance(d.get("email"), str):
                raise ValueError("a user row's 'email' is not a string")
        for c in users.get("user_webauthn_credentials", []):
            _build(UserWebAuthnCredential, c, overrides={"user_id": 0},
                   skip=frozenset({"id", "user_id"}))
        groups = p.get("groups", {})
        for d in groups.get("groups", []):
            _ = (d["id"], d["name"], d["name_normalized"])
        for m in groups.get("group_members", []):
            _ = (m["group_id"], m["user_id"])
        ow = p.get("oidc_webhooks", {})
        for d in ow.get("oidc_providers", []):
            d2 = dict(d)
            _ingest_secret_str(d2.pop("client_secret_encrypted", None))
            _build(OIDCProvider, d2, overrides={
                "client_secret_encrypted": "", "created_by_id": actor.id,
                "updated_by_id": actor.id,
            })
        for d in ow.get("webhooks", []):
            d2 = dict(d)
            _ingest_secret_str(d2.pop("secret_encrypted", None))
            _build(Webhook, d2, overrides={"secret_encrypted": "", "created_by_id": actor.id},
                   skip=frozenset({"id"}))
        # settings_branding and logs are consumed at steps 6-7, i.e. AFTER the
        # irreversible share invalidation has committed. Leaving them unchecked
        # meant a missing `key` raised a bare KeyError there and produced exactly
        # the wipe-then-500 this function exists to prevent (audit 2026-07-30).
        sb = p.get("settings_branding", {})
        for row in sb.get("app_settings", []):
            _ = row["key"]
            if row.get("is_encrypted"):
                _ingest_secret_str(row.get("secret"))
        for o in sb.get("email_template_overrides", []):
            _build(EmailTemplateOverride, o, overrides={"updated_by_id": actor.id},
                   skip=frozenset({"id", "updated_by_id"}))
        logo = sb.get("branding_logo")
        if logo and logo.get("present"):
            for b64_key in ("original_b64", "png_b64"):
                if logo.get(b64_key):
                    base64.b64decode(logo[b64_key], validate=True)
        lg = p.get("logs", {})
        for model, key in (
            (AuditLog, "audit_log"),
            (EmailLog, "email_log"),
            (DownloadLog, "download_log"),
            (LoginAttempt, "login_attempts"),
            (Notification, "notifications"),
        ):
            for d in lg.get(key, []):
                _build(model, d, skip=frozenset({"id"}))
        for pref_row in lg.get("notifications", []):
            NotificationCategory(pref_row["category"])
    except AppError:
        raise
    except (KeyError, ValueError, TypeError) as e:
        raise AppError(
            400, "BACKUP_CORRUPT",
            f"Backup payload is malformed and was not applied (nothing changed): {e}",
        ) from e


def apply_backup(db: Session, *, parsed: ParsedBackup, actor: User, request=None) -> ImportSummary:
    # Users whose FK-bound rows mean a plain delete cannot work: erased AFTER
    # the import commits, because erase_user owns its own transaction (see
    # _purge_user).
    deferred_erasures: list[int] = []
    p = parsed.payload
    # Reject a malformed-but-parseable payload up front, BEFORE anything
    # destructive runs - the share invalidation below is irreversible (audit M6).
    _validate_backup_payload(p, actor)
    has_users = "users" in p
    has_groups = "groups" in p
    warnings = list(parsed.warnings)
    summary = ImportSummary(
        dry_run=False, secret_mode=parsed.secret_mode, categories=parsed.categories,
        version_warning=_version_warning(db, parsed),
        env_snapshot_present="env_snapshot" in p,
    )

    # High-water mark taken before anything destructive: everything the import
    # writes from here on is identified by a larger id and survives the step-7
    # audit_log wipe.
    audit_watermark = db.query(func.max(AuditLog.id)).scalar() or 0
    # Captured before the identity upsert overwrites the actor's row; used by
    # the anti-lockout re-assert below.
    actor_password_hash = actor.password_hash

    # 1. Invalidate ALL active shares in its own committed pass - disk unlink is
    # irreversible and must not sit inside the config transaction.
    inv = share_svc.invalidate_all_active_shares(db, actor=actor, request=request)
    db.commit()
    # Only now, with the rows durable, are the bytes unlinked: a failed commit
    # must not leave shares still marked active over files that are gone.
    from . import file as file_svc
    file_svc.purge_expired_bytes(db, inv["to_purge"], reason="config_restore")
    summary.shares_to_invalidate = inv["expired_shares"]
    summary.files_deleted = inv["deleted_files"]

    user_id_map: dict[int, int] = {}
    group_id_map: dict[int, int] = {}

    # 2. Standalone config tables - wipe + reload.
    if "oidc_webhooks" in p:
        ow = p["oidc_webhooks"]
        db.query(OIDCProvider).delete(synchronize_session=False)
        db.flush()
        for d in ow.get("oidc_providers", []):
            d = dict(d)
            secret = _ingest_secret_str(d.pop("client_secret_encrypted", None))
            db.add(_build(OIDCProvider, d, overrides={
                "client_secret_encrypted": secret or "",
                "created_by_id": actor.id, "updated_by_id": actor.id,
            }))
        db.flush()
        db.query(Webhook).delete(synchronize_session=False)
        db.flush()
        for d in ow.get("webhooks", []):
            d = dict(d)
            secret = _ingest_secret_str(d.pop("secret_encrypted", None))
            db.add(_build(Webhook, d, overrides={
                "secret_encrypted": secret or "", "created_by_id": actor.id,
            }, skip=frozenset({"id"})))
        db.flush()
        summary.counts["oidc_providers"] = len(ow.get("oidc_providers", []))
        summary.counts["webhooks"] = len(ow.get("webhooks", []))

    # 3. Identity upsert (users, then groups) with old->new ID remap.
    existing_oidc_ids = {r[0] for r in db.query(OIDCProvider.id).all()}
    if has_users:
        backup_users = p["users"].get("users", [])
        inserted = updated = 0
        for d in backup_users:
            bid = d.get("id")
            email = normalize_email(d["email"])
            oidc_pid = d.get("oidc_provider_id")
            if oidc_pid and oidc_pid not in existing_oidc_ids:
                oidc_pid = None
            fields = _user_fields(d)
            u = db.query(User).filter(User.email == email).one_or_none()
            if u is None:
                u = User(email=email, oidc_provider_id=oidc_pid, **fields)
                db.add(u)
                db.flush()
                inserted += 1
            else:
                for k, v in fields.items():
                    setattr(u, k, v)
                u.oidc_provider_id = oidc_pid
                updated += 1
            user_id_map[bid] = u.id
        # second pass: created_by_id (self-FK)
        for d in backup_users:
            local = user_id_map.get(d.get("id"))
            cby = user_id_map.get(d.get("created_by_id"))
            if local is not None:
                db.query(User).filter(User.id == local).update(
                    {"created_by_id": cby}, synchronize_session=False
                )
        # Never let a restore downgrade or disable the admin performing it - the
        # upsert above may have overwritten their row with the backup's role /
        # is_disabled. Re-assert so they can finish (and repeat) the import and
        # the org isn't locked out of admin (audit L11).
        me = db.query(User).filter(User.id == actor.id).one_or_none()
        if me is not None:
            me.role = UserRole.admin
            me.is_disabled = False
            # Everything else that gates getting back IN. The upsert above
            # overwrites the actor's whole row from the backup, so a backup
            # taken when they had not yet verified their address (or that
            # carries a different password hash, or no TOTP where they now have
            # one enforced) locked the importing admin out of the instance they
            # were mid-restore on - with every other admin already purged.
            # Restoring role without restoring the ability to authenticate was
            # half a guard (audit 2026-07-30).
            me.email_verified = True
            if actor_password_hash:
                me.password_hash = actor_password_hash
            db.flush()
        summary.counts["users_insert"] = inserted
        summary.counts["users_update"] = updated

    if has_groups:
        for d in p["groups"].get("groups", []):
            nn = d["name_normalized"]
            cby = user_id_map.get(d.get("created_by_id")) or actor.id
            g = db.query(Group).filter(Group.name_normalized == nn).one_or_none()
            if g is None:
                g = Group(
                    name=d["name"], name_normalized=nn, description=d.get("description"),
                    is_company_inbox=bool(d.get("is_company_inbox")), created_by_id=cby,
                )
                db.add(g)
                db.flush()
            else:
                g.name = d["name"]
                g.description = d.get("description")
                g.is_company_inbox = bool(d.get("is_company_inbox"))
            group_id_map[d["id"]] = g.id
        db.flush()
        affected = set(group_id_map.values())
        if affected:
            db.query(GroupMember).filter(
                GroupMember.group_id.in_(affected)
            ).delete(synchronize_session=False)
            db.flush()
        existing_user_ids = {r[0] for r in db.query(User.id).all()}
        for m in p["groups"].get("group_members", []):
            gid = group_id_map.get(m["group_id"])
            uid = user_id_map.get(m["user_id"]) if has_users else (
                m["user_id"] if m["user_id"] in existing_user_ids else None
            )
            if gid is None or uid is None:
                continue
            db.add(GroupMember(group_id=gid, user_id=uid, joined_at=_dt(m.get("joined_at"))))
        db.flush()
        summary.counts["groups"] = len(group_id_map)

    # 3b. Client<->employee connections. Restore the sticky invite-source rows
    # (remapped) and recompute the derivable shared_group rows from the memberships
    # applied above - neither survives a raw users+groups restore otherwise.
    if has_users:
        db.query(ClientEmployeeConnection).delete(synchronize_session=False)
        db.flush()
        for c in p["users"].get("client_employee_connections", []):
            cid = user_id_map.get(c["client_user_id"])
            eid = user_id_map.get(c["employee_user_id"])
            if cid is None or eid is None:
                continue
            db.add(ClientEmployeeConnection(
                client_user_id=cid, employee_user_id=eid,
                source=ConnectionSource.invite,
                created_at=_dt(c.get("created_at")) or utc_now(),
            ))
        db.flush()
        for local_uid in set(user_id_map.values()):
            u = db.query(User).filter(User.id == local_uid).one_or_none()
            if u is not None:
                recompute_shared_group_connections_for_user(db, user=u)
        db.flush()

    # 4. User sub-tables (2FA + notification prefs) - delete-for-affected + insert.
    if has_users:
        local_ids = set(user_id_map.values())
        # NOT the importing admin's. Their sessions are revoked at the end of
        # the import, so the very next thing they do is log in - and a DR
        # rebuild restores a backup from the OLD server, whose TOTP secret is on
        # a phone that no longer exists and whose ten recovery-code hashes are
        # equally stale. Their password is preserved (the user upsert keeps it),
        # so they would reach TOTP_REQUIRED against a secret they cannot produce,
        # with every other admin purged or replaced by the backup and no in-app
        # way back: recovery was a `docker compose exec` to delete a row
        # (audit #2).
        local_ids.discard(actor.id)
        if local_ids:
            db.query(UserTOTP).filter(UserTOTP.user_id.in_(local_ids)).delete(synchronize_session=False)
            db.query(UserRecoveryCode).filter(UserRecoveryCode.user_id.in_(local_ids)).delete(synchronize_session=False)
            db.query(UserWebAuthnCredential).filter(UserWebAuthnCredential.user_id.in_(local_ids)).delete(synchronize_session=False)
            db.query(UserNotificationPreference).filter(UserNotificationPreference.user_id.in_(local_ids)).delete(synchronize_session=False)
            db.flush()
        for t in p["users"].get("user_totp", []):
            uid = user_id_map.get(t["user_id"])
            if uid == actor.id:
                continue
            sec = _ingest_secret_totp(t.get("secret"))
            if uid is None or sec is None:
                continue
            db.add(UserTOTP(
                user_id=uid, secret_encrypted=sec, enabled_at=_dt(t.get("enabled_at")),
                last_used_counter=t.get("last_used_counter", 0),
            ))
        for r in p["users"].get("user_recovery_codes", []):
            uid = user_id_map.get(r["user_id"])
            if uid is None or uid == actor.id:
                continue
            db.add(UserRecoveryCode(
                user_id=uid, code_hash=r["code_hash"],
                created_at=_dt(r.get("created_at")) or utc_now(), used_at=_dt(r.get("used_at")),
            ))
        for c in p["users"].get("user_webauthn_credentials", []):
            uid = user_id_map.get(c["user_id"])
            if uid is None or uid == actor.id:
                continue
            db.add(_build(UserWebAuthnCredential, c, overrides={"user_id": uid},
                          skip=frozenset({"id", "user_id"})))
        for pref in p["users"].get("user_notification_preferences", []):
            uid = user_id_map.get(pref["user_id"])
            if uid is None:
                continue
            db.add(UserNotificationPreference(
                user_id=uid, category=NotificationCategory(pref["category"]),
                channel=NotificationChannel(pref["channel"]),
            ))
        db.flush()

    # 5. Purge identities absent from the backup (literal replace).
    if has_users:
        backup_emails = {normalize_email(u["email"]) for u in p["users"].get("users", [])}
        for u in list(db.query(User).all()):
            if u.email in backup_emails:
                continue
            if u.id == actor.id:
                warnings.append(
                    "the importing admin was not in the backup; account kept to "
                    "preserve the audit trail"
                )
                continue
            # `groups.created_by_id` is ON DELETE CASCADE, so hard-deleting a
            # user takes every group they created with them - including groups
            # this very import restored moments earlier, because group identity
            # is the normalised NAME while ownership is a user id that gets
            # remapped. The import therefore destroyed part of its own result,
            # silently, and only for groups whose creator happened not to be in
            # the backup (audit 2026-07-30). Reassign to the importing admin
            # first: the group is the artefact worth keeping, and its creator
            # is being erased anyway.
            db.query(Group).filter(Group.created_by_id == u.id).update(
                {"created_by_id": actor.id}, synchronize_session=False
            )
            db.flush()
            outcome = _purge_user(db, u, actor=actor, request=request)
            if outcome == "deferred":
                deferred_erasures.append(u.id)
            summary.purged_users.append(
                u.email if outcome == "deleted" else f"{u.email} ({outcome})"
            )
        # Commit the purge on its own, for the same reason step 1 commits the
        # share invalidation: _purge_user unlinks file bytes and decrements the
        # Redis quota counter, and a rollback brings back neither. A failure
        # anywhere in steps 6-8 used to restore the purged users and their file
        # rows while the bytes were already gone, leaving a system whose
        # downloads 500 and whose quota counters under-report until the hourly
        # reconcile. A partially applied REPLACE import is recoverable by
        # re-running it; unlinked bytes are not.
        db.commit()
    if has_groups:
        backup_nn = {g["name_normalized"] for g in p["groups"].get("groups", [])}
        for g in list(db.query(Group).all()):
            if g.name_normalized in backup_nn:
                continue
            db.query(GroupMember).filter(GroupMember.group_id == g.id).delete(synchronize_session=False)
            summary.purged_groups.append(g.name)
            db.delete(g)
        db.flush()

    # 6. app_settings (+ email templates + logo) - wipe + reload, with JSON-ID
    # remap once identities exist.
    if "settings_branding" in p:
        sb = p["settings_branding"]
        db.query(EmailTemplateOverride).delete(synchronize_session=False)
        db.flush()
        db.query(AppSetting).delete(synchronize_session=False)
        db.flush()
        # `_TRANSIENT_SETTING_KEYS` was applied on EXPORT only, so a backup
        # file that carries those keys - hand-edited, or produced by an older
        # build, or supplied by someone else - planted them verbatim. That
        # includes `maintenance.pending_update`, which the minute drain worker
        # reads and hands straight to release_apply: a config import could
        # therefore trigger a self-update to an attacker-chosen tag, bypassing
        # the `v\d+\.\d+\.\d+` validator that only guards the admin route
        # (audit 2026-07-30). Filter on the way in as well as out.
        skipped_transient = 0
        for row in sb.get("app_settings", []):
            key = row["key"]
            if key in _TRANSIENT_SETTING_KEYS or key in _LOGO_LOCATOR_KEYS:
                skipped_transient += 1
                continue
            if row.get("is_encrypted"):
                stored = _ingest_secret_str(row.get("secret"))
                if stored is None:
                    continue
                db.add(AppSetting(
                    key=key, value=stored, is_encrypted=True,
                    updated_at=utc_now(), updated_by_id=actor.id,
                ))
            else:
                val = row.get("value")
                if has_users and key in _JSON_USER_ID_KEYS:
                    val = _remap_json_ids(val, user_id_map)
                elif has_groups and key in _JSON_GROUP_ID_KEYS:
                    val = _remap_json_ids(val, group_id_map)
                db.add(AppSetting(
                    key=key, value=val, is_encrypted=False,
                    updated_at=utc_now(), updated_by_id=actor.id,
                ))
        db.flush()
        for o in sb.get("email_template_overrides", []):
            db.add(_build(
                EmailTemplateOverride, o,
                overrides={"updated_by_id": actor.id},
                skip=frozenset({"id", "updated_by_id"}),
            ))
        db.flush()
        _import_logo(db, sb.get("branding_logo"), actor=actor, warnings=warnings)
        summary.counts["app_settings"] = len(sb.get("app_settings", []))
        if skipped_transient:
            warnings.append(
                f"skipped {skipped_transient} runtime/transient setting key(s) "
                "carried in the backup (maintenance + IMAP cursors are not portable)"
            )
        summary.counts["email_template_overrides"] = len(sb.get("email_template_overrides", []))

    # 7. Logs - wipe + reload (opt-in).
    if "logs" in p:
        lg = p["logs"]
        # Snapshot BEFORE the wipe: this import's own destruction record plus
        # every erasure receipt (see _preserved_audit_rows).
        preserved_audit = _preserved_audit_rows(db, since_id=audit_watermark)
        _restore_log(db, AuditLog, lg.get("audit_log", []), user_fk="actor_user_id",
                     user_id_map=user_id_map, has_users=has_users)
        _reinsert_preserved_audit(db, preserved_audit)
        _restore_log(db, EmailLog, lg.get("email_log", []), user_fk="recipient_user_id",
                     user_id_map=user_id_map, has_users=has_users,
                     null_fields=("source_log_id",))
        _restore_log(db, DownloadLog, lg.get("download_log", []), user_fk="accessed_by_user_id",
                     user_id_map=user_id_map, has_users=has_users,
                     fk_present_checks=(("file_id", File), ("share_id", Share)))
        _restore_log(db, LoginAttempt, lg.get("login_attempts", []), user_fk=None,
                     user_id_map=user_id_map, has_users=has_users)
        _restore_log(db, Notification, lg.get("notifications", []), user_fk="user_id",
                     user_id_map=user_id_map, has_users=has_users, required_user=True)
        summary.counts["logs"] = {k: len(v) for k, v in lg.items()}

    # 8. Revoke all sessions (identity replaced wholesale).
    if has_users:
        from .jwt_session import revoke_all_user_refresh_tokens

        for (uid,) in db.query(User.id).all():
            summary.sessions_revoked += revoke_all_user_refresh_tokens(db, uid)

    summary.warnings = warnings
    record_audit_event(
        db,
        event_type=AuditEventType.config_backup_imported,
        actor_user_id=actor.id,
        target_type="config_backup",
        target_id=None,
        metadata={
            "categories": parsed.categories,
            "secret_mode": parsed.secret_mode,
            "shares_invalidated": summary.shares_to_invalidate,
            "purged_users": len(summary.purged_users),
            "purged_groups": len(summary.purged_groups),
            "sessions_revoked": summary.sessions_revoked,
            "counts": summary.counts,
        },
        request=request,
    )
    db.commit()

    # Now, outside the import transaction. A failure here leaves the imported
    # configuration intact and is reported rather than silently discarding it.
    for user_id in deferred_erasures:
        target = db.query(User).filter(User.id == user_id).one_or_none()
        if target is None:
            continue
        try:
            erase_user(db, actor=actor, target=target, request=request)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("config import: deferred erasure failed for user=%s", user_id)
            summary.warnings.append(
                f"user {user_id} could not be erased after the import; "
                "their account is still present and disabled"
            )
    return summary
