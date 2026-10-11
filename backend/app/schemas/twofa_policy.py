"""2FA enforcement policy schemas (post-Phase 10)."""
from __future__ import annotations

from pydantic import Field

from .common import APIBaseModel


class RequiredGroupRef(APIBaseModel):
    id: int
    name: str
    is_company_inbox: bool = False


class TwofaPolicyResponse(APIBaseModel):
    required_roles: list[str]                         # subset of admin/employee/client
    required_group_ids: list[int]
    required_groups: list[RequiredGroupRef]           # labels for SPA display
    # True when the kv keys are set (admin saved a policy at least
    # once). False means we're inheriting from the REQUIRE_2FA env
    # knob - surface this to the SPA so the editor can show
    # "currently inheriting from environment" instead of pretending
    # the unsaved form is the truth.
    is_kv_overridden: bool
    # Passkey sign-in (v2.27.0): a passkey alone signs in, and counts as 2FA.
    passkey_signin: bool = False


class UpdateTwofaPolicyRequest(APIBaseModel):
    required_roles: list[str] = Field(default_factory=list)
    required_group_ids: list[int] = Field(default_factory=list)
    # None leaves the switch as it is, so a page that predates it cannot turn
    # it off by saving the policy.
    passkey_signin: bool | None = None
