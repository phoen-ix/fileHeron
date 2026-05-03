"""User account preferences (post-login destination, etc.)."""
from __future__ import annotations

from ..models.user import User

# Allowed values for `users.default_landing_page`. Single source of
# truth — the PATCH endpoint validates against this set, the admin UI
# filters its picker against this set, and the effective-landing
# resolver assumes any value already passed validation.
#
# Only non-admin routes are exposed (per the user's design choice).
# Admin users still navigate to admin pages after landing — they just
# don't auto-go-there.
ALLOWED_LANDING_ROUTES = frozenset(
    {"home", "outbox", "inbox", "share-create", "account"}
)


def effective_landing_route(user: User, *, home_enabled: bool) -> str:
    """Resolve the route name a user should land on post-login (or when
    they hit `/` while home is disabled).

    Order:
    1. If user has a saved preference and it's reachable, use it.
       ("home" requires `home_enabled`.)
    2. Else if home is enabled, use "home".
    3. Else fall back to "share-create".
    """
    pref = user.default_landing_page
    if pref:
        if pref == "home":
            return "home" if home_enabled else "share-create"
        if pref in ALLOWED_LANDING_ROUTES:
            return pref
        # Unknown / stale value (e.g. set before a future rename) —
        # ignore and fall through.
    if home_enabled:
        return "home"
    return "share-create"
