"""L22/L25: the self-update target_tag is constrained to a release-tag shape
before it can flow into `docker pull` / FH_TAG on the host."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.routers.admin.system import UpdateApplyRequest


@pytest.mark.parametrize(
    "bad",
    [
        "latest",
        "v1.2",
        "1.2.3",
        "v1.2.3-rc1",
        "v1.2.3; rm -rf /",
        "$(whoami)",
        "../../etc/passwd",
        "v1.2.3 && curl evil",
    ],
)
def test_rejects_non_release_tags(bad):
    with pytest.raises(ValidationError):
        UpdateApplyRequest(password="x", target_tag=bad)


def test_accepts_release_tags_and_none():
    assert UpdateApplyRequest(password="x", target_tag="v1.35.0").target_tag == "v1.35.0"
    assert UpdateApplyRequest(password="x", target_tag="v10.0.123").target_tag == "v10.0.123"
    assert UpdateApplyRequest(password="x", target_tag=None).target_tag is None
