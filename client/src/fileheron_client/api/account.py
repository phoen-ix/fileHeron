"""Self-service profile endpoints — currently just the locale switch.

v0.8.0: factored out so the desktop language picker has a clean
helper instead of inlining the PATCH call into settings_dialog. As
parity with the SPA's Account.vue grows (display_name, default
landing page, etc.), they belong here too."""
from __future__ import annotations

from .client import ApiClient
from ..models import MeResponse


def patch_locale(api: ApiClient, locale: str) -> MeResponse:
    """PATCH ``/api/account/locale`` body ``{locale: "en"|"de"}``.

    Backend persists to ``users.locale``; subsequent ``/me`` calls
    return the new value. Returns the updated MeResponse so the
    caller can refresh in-memory state without a second round trip.
    """
    out = api.request_or_raise(
        "PATCH", "/api/account/locale", json={"locale": locale},
    )
    return MeResponse.model_validate(out)
