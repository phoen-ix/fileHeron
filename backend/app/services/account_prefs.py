"""User account preferences (post-login destination, etc.)."""
from __future__ import annotations

# Allowed values for `users.default_landing_page`. Single source of
# truth — the PATCH endpoint validates against this set and the admin UI
# filters its picker against this set. Post-login route resolution itself
# lives frontend-side in `composables/useEffectiveLanding.ts`.
#
# Only non-admin routes are exposed (per the user's design choice).
# Admin users still navigate to admin pages after landing — they just
# don't auto-go-there.
ALLOWED_LANDING_ROUTES = frozenset(
    {"home", "outbox", "inbox", "share-create", "account"}
)
