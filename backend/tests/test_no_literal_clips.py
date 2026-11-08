"""Every clip of a value bound for a String column derives its width from the
column (`utils/columns.declared_width`), never from a literal.

CLAUDE.md has said so since the width class recurred four times; this audit
still found twenty literal clips on persisted values (`subject[:255]`,
`display_name[:120]`, eight inbound-mail fields, six mail-log fields, the
telemetry paths). They all matched their columns on the day, and nothing kept
them matching (audit 2026-11-07).

A ratchet: the literal slices that remain in `app/services`, `app/routers` and
`app/workers` (since 2026-11-08 - the workers wrote two literal column clips)
are listed below, each a NON-column use (a hash prefix, a log or JSON message
cap, a filesystem name, a policy cap on a Text column). A new `[:N]` fails here
until it is either derived from its column or classified as one of those.
"""
from __future__ import annotations

import re
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
_LITERAL_CLIP = re.compile(r"\[:[0-9]+\]|\[-[0-9]+:\]")

# (path under app/, the line as it reads) -> known non-column clips.
_KNOWN_NON_COLUMN_CLIPS: set[tuple[str, str]] = {
    ('routers/admin/imap.py', '"attachments": filenames[:20],'),
    ('routers/admin/settings/branding.py', 'content_type = _sniff_image_type(content[:16])'),
    ('routers/admin/system.py', 'out["av"] = {"status": "down", "error": str(e)[:200]}'),
    ('routers/admin/system.py', 'out["redis"] = {"status": "down", "error": str(e)[:200]}'),
    ('routers/files.py', 'f"share-{share.id[:8]}",'),
    ('routers/oidc_connect.py', 'return f"…{sub[-4:]}"'),
    ('routers/public.py', 'f"share-{share.id[:8]}",'),
    ('services/api_token.py', 'last4=secret[-4:],'),
    ('services/auto_update.py', 'f"starts once running ones finish, at the latest {deadline[:16]} UTC."),'),
    ('services/auto_update.py', 'if settings.scope == "patch" and latest_key[:2] != running_key[:2]:'),
    ('services/cron_tracker.py', '"error": error_msg[:500],'),
    ('services/cron_tracker.py', '"message": error_msg[:500],'),
    ('services/cron_tracker.py', 'metadata={"error": error_msg[:500], "duration_ms": duration_ms},'),
    ('services/cron_tracker.py', 'result["error"] = error[:500]'),
    ('services/file_admin.py', 'parts.extend(f"{g.name} (group)" for g in group_recipients[:1])'),
    (
        'services/file_admin.py',
        'parts: list[str] = [f"{u.display_name} ({u.role.value})" for u in user_recipients[:1]]',
    ),
    ('services/file_encryption.py', 'digest = hashlib.sha256(f"{kind}:{row_id}:{size}".encode()).hexdigest()[:32]'),
    ('services/hibp.py', 'prefix, suffix = sha1[:5], sha1[5:]'),
    ('services/imap_poll.py', '"error": f"{type(exc).__name__}: {str(exc)[:200]}",'),
    ('services/imap_poll.py', 'text = str(exc)[:300]'),
    ('services/job_queue.py', 'f"{name}[{_redact(args, kwargs)}]" for name, args, kwargs in jobs[:3]'),
    ('services/maintenance.py', 'error = (job.get("error") or "")[:500] or None'),
    ('services/quarantine.py', 'metadata={"reason": "quarantine_dispatch_failed", "error": str(e)[:300]},'),
    ('services/quarantine.py', 'scrubbed = name.replace("/", "_").replace("\\\\", "_").replace("\\0", "")[:240]'),
    ('services/rate_limit.py', 'return f"fh:rl:{bucket}:ip:{sha256_hex(ip)[:16]}"'),
    ('services/release_apply.py', 'return [line[:2000] for line in value if isinstance(line, str)][-200:]'),
    ('services/release_apply.py', 'return [w[:500] for w in value if isinstance(w, str)][:20]'),
    ('services/release_apply.py', 'return value[:500] if isinstance(value, str) else None'),
    ('services/release_check.py', 'return f"{type(e).__name__}: {detail[:200]}"'),
    ('services/scan_guard.py', 'f"fh:scanguard:paths:{sha256_hex(ip)[:16]}",'),
    ('services/scan_guard.py', 'key = f"fh:scanguard:paths:{sha256_hex(ip)[:16]}"'),
    ('services/share_approval.py', 'return hashlib.sha256(raw.encode()).hexdigest()[:16]'),
    ('services/storage_backend.py', '"message": f"stored file {ref} failed authentication: {exc}"[:500],'),
    ('services/storage_backend.py', 'metadata={"error": str(exc)[:200]},'),
    ('services/zip_writer.py', 'return h.hexdigest()[:32]'),
    # app/workers: JSON payload / result-dict caps and a batch cap, no column.
    ('workers/auto_update.py', 'return {CRON_FAILED_KEY: True, "error": f"{type(exc).__name__}: {exc}"[:500]}'),
    ('workers/disk_check.py', '"detail": str(stats.get("error"))[:200],'),
    ('workers/ops_check.py', 'payload = {"reason": reason, "detail": detail[:500], "at": utc_now().isoformat()}'),
    ('workers/rescan_inbound_attachments.py', 'q = q.filter(InboundAttachment.id.notin_(list(deferred)[:1000]))'),
    ('workers/send_email.py', '"subject": subject[:120],'),
    ('workers/send_email.py', '"smtp_message": (message or "")[:300],'),
}


def _literal_clips():
    for sub in ("services", "routers", "workers"):
        for path in sorted((_APP / sub).rglob("*.py")):
            for text in path.read_text().splitlines():
                stripped = text.strip()
                if stripped.startswith(("#", '"""')) or "``" in stripped:
                    continue
                if _LITERAL_CLIP.search(stripped):
                    yield str(path.relative_to(_APP)), stripped


def test_every_literal_clip_is_a_known_non_column_use():
    unknown = [f"{p}: {t}" for p, t in _literal_clips() if (p, t) not in _KNOWN_NON_COLUMN_CLIPS]
    assert unknown == [], "\n".join(
        ["derive the width from the column (declared_width) or classify the clip:", *unknown]
    )


def test_the_allowlist_carries_no_stale_entries():
    present = set(_literal_clips())
    stale = sorted(_KNOWN_NON_COLUMN_CLIPS - present)
    assert stale == [], f"no longer in the code, drop from the list: {stale}"
