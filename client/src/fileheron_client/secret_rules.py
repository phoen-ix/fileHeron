"""The rules behind the secret forms (server v2.24.0), free of Tk.

The compose views stay thin: what blocks a Send, which expiry presets the
admin's ceilings allow, which view scopes mean something, how typed addresses
are read, and the password generator all live here, where the Linux test leg
(which has no Tk) can reach them. Mirrors the web app's SecretCreate /
SecretRequestCreate and utils/passwordGenerator.ts.

A blocker is an i18n key plus its format arguments; the view renders
``t(key, **kwargs)`` and disables Send exactly while the list is non-empty
(the ShareCreate rule: a visible list, never a hidden condition).
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Optional, Sequence

from .models import MeResponse, SecretLimitsResponse

MIN_PASSPHRASE = 8
MAX_CONTENT = 10_000
MAX_LABEL = 200
MAX_NOTE = 1000
MAX_EMAILS = 20
MAX_EMAIL_LEN = 254

HOUR_S = 3600
DAY_S = 24 * HOUR_S

# The web app's fallback when /me carries no limits.
DEFAULT_LIMITS = SecretLimitsResponse(
    max_views=100,
    max_expiry_days=90,
    max_lifetime_days=90,
    passphrase_failure_mode="lock",
    passphrase_max_failures=10,
)

# The web RecipientPicker's address check.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_ADDRESS_SPLIT_RE = re.compile(r"[\s,;]+")

Blocker = tuple[str, dict]


def limits_of(me: MeResponse) -> SecretLimitsResponse:
    return me.secret_limits or DEFAULT_LIMITS


# ---- presets -----------------------------------------------------------------

# (i18n key suffix, seconds). The keys are `secrets.preset.<suffix>`.
_SECRET_EXPIRY = (("1h", HOUR_S), ("1d", DAY_S), ("7d", 7 * DAY_S),
                  ("30d", 30 * DAY_S), ("90d", 90 * DAY_S))
_REQUEST_OPEN = (("1d", DAY_S), ("7d", 7 * DAY_S), ("30d", 30 * DAY_S), ("90d", 90 * DAY_S))
_ANSWER_LIFETIME = _SECRET_EXPIRY

DEFAULT_PRESET = "7d"
NEVER = "never"


def _within(presets: Sequence[tuple[str, int]], max_days: int) -> list[tuple[str, Optional[int]]]:
    return [(k, s) for k, s in presets if s <= max_days * DAY_S]


def secret_expiry_presets(limits: SecretLimitsResponse) -> list[tuple[str, Optional[int]]]:
    """A secret's expiry: the presets the admin's ceiling allows, then Never
    (allowed only with a view limit - see `secret_blockers`)."""
    return _within(_SECRET_EXPIRY, limits.max_expiry_days) + [(NEVER, None)]


def request_open_presets(limits: SecretLimitsResponse) -> list[tuple[str, Optional[int]]]:
    """How long a request stays open. Never "no expiry": a request always
    closes."""
    return _within(_REQUEST_OPEN, limits.max_expiry_days)


def answer_lifetime_options(limits: SecretLimitsResponse) -> list[tuple[str, Optional[int]]]:
    """How long the answer lives after it arrives; the server caps it at the
    expiry ceiling. `never` = no time limit (then a view limit is needed)."""
    return _within(_ANSWER_LIFETIME, limits.max_expiry_days) + [(NEVER, None)]


def default_preset(presets: Sequence[tuple[str, Optional[int]]]) -> str:
    """7 days when the ceiling allows it, else the longest timed preset."""
    keys = [k for k, _ in presets]
    if DEFAULT_PRESET in keys:
        return DEFAULT_PRESET
    timed = [k for k, s in presets if s is not None]
    return timed[-1] if timed else keys[0]


def preset_seconds(presets: Sequence[tuple[str, Optional[int]]], key: str) -> Optional[int]:
    for k, s in presets:
        if k == key:
            return s
    raise KeyError(key)


def scope_options(has_group: bool) -> list[str]:
    """Without a group, "each person" and "each recipient" are the same thing -
    offer the choice only when it means something."""
    return ["per_person", "per_recipient", "total"] if has_group else ["per_person", "total"]


# ---- addresses ---------------------------------------------------------------


def parse_addresses(text: str) -> tuple[list[str], list[str]]:
    """Split typed addresses on commas, semicolons, spaces and new lines.
    Returns (valid, invalid), each de-duplicated case-insensitively and in the
    order typed. The server normalises what it stores; nothing here looks an
    address up."""
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for raw in _ADDRESS_SPLIT_RE.split(text or ""):
        addr = raw.strip()
        if not addr or addr.casefold() in seen:
            continue
        seen.add(addr.casefold())
        if len(addr) <= MAX_EMAIL_LEN and _EMAIL_RE.match(addr):
            valid.append(addr)
        else:
            invalid.append(addr)
    return valid, invalid


# ---- blockers ----------------------------------------------------------------


@dataclass
class SecretForm:
    content: str = ""
    label: str = ""
    has_picked: bool = False  # a user or group picked
    addresses: str = ""
    create_link: bool = False
    can_external: bool = False
    limit_views: bool = True
    max_views: str = "1"
    expiry_key: str = DEFAULT_PRESET
    passphrase: str = ""
    passphrase_repeat: str = ""


@dataclass
class RequestForm:
    label: str = ""
    has_picked: bool = False
    addresses: str = ""
    create_link: bool = False
    can_external: bool = False
    limit_views: bool = True
    max_views: str = "1"
    lifetime_key: str = DEFAULT_PRESET
    passphrase: str = ""
    passphrase_repeat: str = ""


def _parse_views(text: str) -> Optional[int]:
    try:
        v = int((text or "").strip())
    except ValueError:
        return None
    return v if v >= 1 else None


def _audience_blockers(
    has_picked: bool, addresses: str, create_link: bool, can_external: bool,
    *, none_key: str, none_or_link_key: str,
) -> list[Blocker]:
    out: list[Blocker] = []
    valid, invalid = parse_addresses(addresses) if can_external else ([], [])
    for addr in invalid:
        out.append(("secrets.blockers.address_invalid", {"q": addr}))
    if len(valid) > MAX_EMAILS:
        out.append(("secrets.blockers.too_many_addresses", {"max": MAX_EMAILS}))
    if not invalid and not has_picked and not valid and not (create_link and can_external):
        out.append((none_or_link_key if can_external else none_key, {}))
    return out


def _views_blockers(limit_views: bool, max_views: str, limits: SecretLimitsResponse) -> list[Blocker]:
    if not limit_views:
        return []
    v = _parse_views(max_views)
    if v is None:
        return [("secrets.blockers.views_invalid", {})]
    if v > limits.max_views:
        return [("secrets.blockers.views_too_many", {"max": limits.max_views})]
    return []


def _passphrase_blockers(passphrase: str, repeat: str) -> list[Blocker]:
    if not passphrase:
        return []
    if len(passphrase) < MIN_PASSPHRASE:
        return [("secrets.blockers.passphrase_short", {"n": MIN_PASSPHRASE})]
    if passphrase != repeat:
        return [("secrets.blockers.passphrase_mismatch", {})]
    return []


def secret_blockers(form: SecretForm, limits: SecretLimitsResponse) -> list[Blocker]:
    out: list[Blocker] = []
    if not form.content:
        out.append(("secrets.blockers.no_content", {}))
    elif len(form.content) > MAX_CONTENT:
        out.append(("secrets.blockers.too_long", {}))
    if len(form.label.strip()) > MAX_LABEL:
        out.append(("secrets.blockers.label_too_long", {"max": MAX_LABEL}))
    out += _audience_blockers(
        form.has_picked, form.addresses, form.create_link, form.can_external,
        none_key="secrets.blockers.no_recipient",
        none_or_link_key="secrets.blockers.no_recipient_or_link",
    )
    if form.expiry_key == NEVER and not form.limit_views:
        out.append(("secrets.blockers.no_limit", {}))
    out += _views_blockers(form.limit_views, form.max_views, limits)
    out += _passphrase_blockers(form.passphrase, form.passphrase_repeat)
    return out


def request_blockers(form: RequestForm, limits: SecretLimitsResponse) -> list[Blocker]:
    out: list[Blocker] = []
    if not form.label.strip():
        out.append(("secrets.blockers.no_label", {}))
    elif len(form.label.strip()) > MAX_LABEL:
        out.append(("secrets.blockers.label_too_long", {"max": MAX_LABEL}))
    out += _audience_blockers(
        form.has_picked, form.addresses, form.create_link, form.can_external,
        none_key="secrets.blockers.no_target",
        none_or_link_key="secrets.blockers.no_target_or_link",
    )
    if form.lifetime_key == NEVER and not form.limit_views:
        out.append(("secrets.blockers.no_answer_limit", {}))
    out += _views_blockers(form.limit_views, form.max_views, limits)
    out += _passphrase_blockers(form.passphrase, form.passphrase_repeat)
    return out


def answer_blockers(content: str, passphrase: str, repeat: str) -> list[Blocker]:
    out: list[Blocker] = []
    if not content:
        out.append(("secrets.blockers.no_content", {}))
    elif len(content) > MAX_CONTENT:
        out.append(("secrets.blockers.too_long", {}))
    out += _passphrase_blockers(passphrase, repeat)
    return out


def views_value(limit_views: bool, max_views: str) -> Optional[int]:
    """The `max_views` to send: None when views are not limited."""
    return _parse_views(max_views) if limit_views else None


# ---- password generator ------------------------------------------------------

PASSWORD_LENGTH = 20
_AMBIGUOUS = set("0O1lI|`'\"")
_SETS = (
    "abcdefghijklmnopqrstuvwxyz",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "0123456789",
    "!#$%&*+-=?@^_~.,:;()[]{}<>/",
)
# Classes with the look-alikes removed (the web generator's default).
PASSWORD_CLASSES = tuple("".join(c for c in s if c not in _AMBIGUOUS) for s in _SETS)

_SYSTEM_RANDOM = secrets.SystemRandom()


def generate_password(length: int = PASSWORD_LENGTH, rng=None) -> str:
    """A random password using every class, without look-alike characters.
    Every class is guaranteed by drawing again, never by planting a character
    at a predictable position. ``rng`` is for tests; the app always uses the
    OS's CSPRNG."""
    rng = rng or _SYSTEM_RANDOM
    alphabet = "".join(PASSWORD_CLASSES)
    length = max(MIN_PASSPHRASE, min(64, int(length)))
    while True:
        out = "".join(rng.choice(alphabet) for _ in range(length))
        if all(any(c in cls for c in out) for cls in PASSWORD_CLASSES):
            return out
