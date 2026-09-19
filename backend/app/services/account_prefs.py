"""User account preferences (post-login destination, etc.)."""
from __future__ import annotations

# Allowed values for `users.default_landing_page`. Single source of
# truth - the PATCH endpoint validates against this set and the admin UI
# filters its picker against this set. Post-login route resolution itself
# lives frontend-side in `composables/useEffectiveLanding.ts`.
#
# Only non-admin routes are exposed (per the user's design choice).
# Admin users still navigate to admin pages after landing - they just
# don't auto-go-there.
ALLOWED_LANDING_ROUTES = frozenset(
    {"home", "outbox", "inbox", "share-create", "account"}
)

# Admin sidebar collapse behaviour for `users.admin_nav_collapse_mode`.
# NULL on the column means "system default" (accordion); these are the
# explicit choices. Single source of truth - the PATCH endpoint validates
# against this set.
ADMIN_NAV_MODES = frozenset({"expanded", "accordion", "manual"})

# Valid sidebar category keys for `users.admin_nav_open_categories`, in
# canonical display order (the order normalizes the stored open-set so writes
# are deterministic). The PATCH endpoint rejects any key not in the set; an
# empty set is a valid explicit value (all categories collapsed). Mirrors
# `frontend/src/config/adminNav.ts::ADMIN_CATEGORY_KEYS` - pinned by
# `tests/test_admin_nav_categories_pin.py`, which reads both files.
# Persisted values holding keys from an earlier taxonomy (v2.16: access /
# sharing / messaging / system) need no migration: the frontend's seed() drops
# unknown keys and GET never re-validates; the next toggle rewrites the list.
ADMIN_NAV_CATEGORIES_ORDER = ("people", "sharing", "email", "security", "site", "system")
ADMIN_NAV_CATEGORIES = frozenset(ADMIN_NAV_CATEGORIES_ORDER)
