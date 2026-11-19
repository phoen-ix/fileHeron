"""The executor acts on the job the shim validated, not on a later rewrite
(audit 2026-11-19).

The shim refuses a downgrade and binds a rollback to the version it recorded,
then blocks on cosign and the executor pull - while the backend, which owns
/state, can still rewrite the job file. The executor re-read it and checked
only the tag's shape, so a swapped tag or action got the verified NEW executor
to deploy an older signed release. The shim now passes the validated id, tag
and action as env; the executor refuses a file that disagrees.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

SHIM = Path(__file__).resolve().parents[3] / "docker" / "updater-shim" / "shim.sh"


class _ReachedError(Exception):
    """Raised by the first step after the hand-over check."""


def _stop():
    raise _ReachedError


def _handover(monkeypatch, *, job_id="j1", tag="v2.31.0", action="update"):
    monkeypatch.setenv("EXECUTOR_JOB_ID", job_id)
    monkeypatch.setenv("EXECUTOR_TARGET_TAG", tag)
    monkeypatch.setenv("EXECUTOR_ACTION", action)


def _write_job(executor, **job):
    executor.STATE_FILE.write_text(json.dumps({"status": "claiming", **job}))


def test_the_shim_hands_over_exactly_what_it_validated():
    src = SHIM.read_text(encoding="utf-8")
    start = src.index("docker run --rm \\")
    block = src[start: src.index('"$executor_image" &', start)]
    for line in (
        '-e "EXECUTOR_JOB_ID=$job_id"',
        '-e "EXECUTOR_TARGET_TAG=$target_tag"',
        '-e "EXECUTOR_ACTION=$action"',
    ):
        assert line in block, line


@pytest.mark.parametrize(
    "swapped",
    [
        {"id": "j1", "target_tag": "v2.29.0", "action": "update"},    # a downgrade
        {"id": "j1", "target_tag": "v2.31.0", "action": "rollback"},  # an unbound rollback
        {"id": "other", "target_tag": "v2.31.0", "action": "update"}, # another job
    ],
)
def test_a_job_rewritten_after_validation_is_refused(executor, monkeypatch, swapped):
    _handover(monkeypatch)
    _write_job(executor, **swapped)
    monkeypatch.setattr(executor, "read_current_tag", _stop)

    assert executor.main() == 1
    state = json.loads(executor.STATE_FILE.read_text())
    assert state["status"] == "failed"
    assert "changed after it was validated" in state["error"]


def test_the_validated_job_proceeds(executor, monkeypatch):
    _handover(monkeypatch)
    _write_job(executor, id="j1", target_tag="v2.31.0", action="update")

    monkeypatch.setattr(executor, "read_current_tag", _stop)
    with pytest.raises(_ReachedError):
        executor.main()


def test_a_missing_action_reads_as_update_like_the_shims_jq(executor, monkeypatch):
    _handover(monkeypatch, action="update")
    _write_job(executor, id="j1", target_tag="v2.31.0", action=None)

    monkeypatch.setattr(executor, "read_current_tag", _stop)
    with pytest.raises(_ReachedError):
        executor.main()


def test_an_older_shim_without_the_env_keeps_the_job_file(executor, monkeypatch):
    for name in ("EXECUTOR_JOB_ID", "EXECUTOR_TARGET_TAG", "EXECUTOR_ACTION"):
        monkeypatch.delenv(name, raising=False)
    _write_job(executor, id="j1", target_tag="v2.31.0", action="update")

    monkeypatch.setattr(executor, "read_current_tag", _stop)
    with pytest.raises(_ReachedError):
        executor.main()


# --- the shim's rules, enforced by the executor itself -----------------------
#
# An older shim supervises the update that INSTALLS this executor and passes no
# hand-over env, so the executor re-checks the two rules on its own: the
# running version from the docker daemon's label, the rollback target from the
# shim's private record (inside the checkout the executor mounts).


def _record(executor, tag):
    private = executor.WORKSPACE / "data" / "updater-private"
    private.mkdir(parents=True, exist_ok=True)
    (private / "rollback_target.json").write_text(json.dumps({"tag": tag}))


def test_an_update_below_the_running_version_is_refused_without_any_env(executor, monkeypatch):
    monkeypatch.setattr(executor, "_daemon_backend_version", lambda: "v2.31.0")
    monkeypatch.setattr(executor, "read_current_tag", _stop)
    _write_job(executor, id="j1", target_tag="v2.30.9", action="update")

    assert executor.main() == 1
    state = json.loads(executor.STATE_FILE.read_text())
    assert state["status"] == "failed"
    assert state["error"].startswith("downgrade refused: v2.30.9 is older than the running v2.31.0")


@pytest.mark.parametrize("target", ["v2.31.0", "v2.31.1", "v3.0.0"])
def test_a_redeploy_or_an_upgrade_passes(executor, monkeypatch, target):
    monkeypatch.setattr(executor, "_daemon_backend_version", lambda: "v2.31.0")
    monkeypatch.setattr(executor, "read_current_tag", _stop)
    _write_job(executor, id="j1", target_tag=target, action="update")
    with pytest.raises(_ReachedError):
        executor.main()


def test_an_unknown_running_version_skips_the_downgrade_rule(executor, monkeypatch):
    monkeypatch.setattr(executor, "_daemon_backend_version", lambda: None)
    monkeypatch.setattr(executor, "read_current_tag", _stop)
    _write_job(executor, id="j1", target_tag="v1.0.0", action="update")
    with pytest.raises(_ReachedError):
        executor.main()


def test_a_rollback_is_bound_to_the_shims_record(executor, monkeypatch):
    monkeypatch.setattr(executor, "read_current_tag", _stop)
    _record(executor, "v2.30.2")

    _write_job(executor, id="j1", target_tag="v2.29.0", action="rollback")
    assert executor.main() == 1
    state = json.loads(executor.STATE_FILE.read_text())
    assert "not the version the shim recorded" in state["error"]

    _write_job(executor, id="j2", target_tag="v2.30.2", action="rollback")
    with pytest.raises(_ReachedError):
        executor.main()


def test_an_unrecorded_rollback_stands_as_in_the_shim(executor, monkeypatch):
    monkeypatch.setattr(executor, "read_current_tag", _stop)
    _write_job(executor, id="j1", target_tag="v2.29.0", action="rollback")
    with pytest.raises(_ReachedError):
        executor.main()


def test_a_garbled_record_refuses_without_echoing_it(executor, monkeypatch):
    monkeypatch.setattr(executor, "read_current_tag", _stop)
    _record(executor, "v2.30.2\nFH_TAG=evil")
    _write_job(executor, id="j1", target_tag="v2.29.0", action="rollback")
    assert executor.main() == 1
    error = json.loads(executor.STATE_FILE.read_text())["error"]
    assert error == "rollback refused: the shim's rollback record is unreadable"


def test_the_running_version_comes_from_the_daemon_label(executor, monkeypatch):
    seen = []

    def fake_inspect(container, fmt):
        seen.append((container, fmt))
        return "v2.31.0"

    monkeypatch.setattr(executor, "_container_id", lambda service: f"cid-{service}")
    monkeypatch.setattr(executor, "_inspect", fake_inspect)
    assert executor._daemon_backend_version() == "v2.31.0"
    assert seen == [("cid-backend", '{{index .Config.Labels "org.opencontainers.image.version"}}')]

    monkeypatch.setattr(executor, "_inspect", lambda *_a: "0.0.0-dev")
    assert executor._daemon_backend_version() is None


# --- which executor the shim runs --------------------------------------------


def test_the_shim_selects_the_newer_releases_executor():
    """The target's executor is OLDER for a rollback and may predate the
    hand-over check, so a rollback runs the running release's - except for a
    target older than image signing, which keeps its own (the newer executor
    would refuse its unsigned app images). Behaviour pinned in test_updater.py."""
    src = SHIM.read_text(encoding="utf-8")
    pending = src[src.index("        pending)"): src.index("        pulling|running|restarting|claiming|rolling_back)")]
    assert 'executor_tag="$target_tag"' in pending
    assert 'executor_tag="$running"' in pending
    assert '! version_lt "$target_tag" "$FIRST_SIGNED_RELEASE"' in pending
    assert 'FIRST_SIGNED_RELEASE="v2.29.0"' in src
    assert 'fileheron-updater-executor:$executor_tag"' in pending
    assert 'fileheron-updater-executor:$target_tag"' not in pending
    # The unsigned escape hatch stays for a pre-signing target run by its OWN executor.
    assert '[ "$action" = "rollback" ] && [ "$bound" = 1 ] && [ "$executor_tag" = "$target_tag" ]' in pending
