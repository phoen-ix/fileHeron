"""Persistent downloads registry (Resume index across restarts)."""
from __future__ import annotations

from fileheron_client import downloads_registry as reg


def test_registry_lifecycle(tmp_config_dir):
    assert reg.load() == {}
    assert reg.get("f1") is None

    reg.upsert(
        "f1", dest="/tmp/a.bin", filename="a.bin", total=100,
        bytes_done=0, status=reg.ACTIVE, share_id="s1",
    )
    e = reg.get("f1")
    assert e is not None
    assert e["status"] == reg.ACTIVE
    assert e["dest"] == "/tmp/a.bin"
    assert e["total"] == 100
    assert e["share_id"] == "s1"

    reg.set_status("f1", reg.PAUSED, bytes_done=40)
    e = reg.get("f1")
    assert e["status"] == reg.PAUSED
    assert e["bytes_done"] == 40
    assert reg.PAUSED in reg.RESUMABLE

    reg.remove("f1")
    assert reg.get("f1") is None
    assert reg.load() == {}


def test_set_status_unknown_is_noop(tmp_config_dir):
    reg.set_status("ghost", reg.INTERRUPTED, bytes_done=10)  # must not raise
    assert reg.get("ghost") is None


def test_corrupt_registry_degrades(tmp_config_dir):
    (tmp_config_dir / "downloads.json").write_text("{ not valid json", encoding="utf-8")
    assert reg.load() == {}  # degrades instead of crashing
    # A subsequent write recovers cleanly.
    reg.upsert("f", dest="/x", filename="x", total=1)
    assert reg.get("f") is not None


def test_reconcile_on_startup_promotes_active(tmp_config_dir):
    reg.upsert("crashed", dest="/a", filename="a", total=10, status=reg.ACTIVE)
    reg.upsert("paused", dest="/b", filename="b", total=10, status=reg.PAUSED)
    reg.reconcile_on_startup()
    # The leftover 'active' (a crash/force-quit) becomes resumable...
    assert reg.get("crashed")["status"] == reg.INTERRUPTED
    # ...a genuinely-paused one is untouched.
    assert reg.get("paused")["status"] == reg.PAUSED


def test_second_save_supersedes(tmp_config_dir):
    reg.upsert("f", dest="/a", filename="a", total=10, status=reg.PAUSED)
    reg.upsert("f", dest="/b", filename="a", total=10, status=reg.ACTIVE)
    e = reg.get("f")
    assert e["dest"] == "/b"
    assert e["status"] == reg.ACTIVE
