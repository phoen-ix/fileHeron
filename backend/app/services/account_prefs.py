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

# Valid sidebar category keys for `users.admin_nav_open_categories`. The
# PATCH endpoint rejects any key not in this set. An empty set is a valid
# explicit value (all categories collapsed). Keep in sync with the frontend
# nav config (`frontend/src/config/adminNav.ts`).
ADMIN_NAV_CATEGORIES = frozenset({"access", "sharing", "messaging", "system"})

# Canonical display order for the sidebar categories - used to normalize the
# stored open-set so writes are deterministic (frozensets are unordered).
ADMIN_NAV_CATEGORIES_ORDER = ("access", "sharing", "messaging", "system")
