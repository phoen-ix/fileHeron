"""Desktop-client findings from the 2026-07-30 audit.

client-3  the settings overlay ran `logout()` - a blocking HTTP round-trip -
          directly on the Tk main thread, freezing the window for its duration
          and for the full httpx timeout when the server was unreachable, which
          is one of the moments a user most wants to sign out.
client-4  direct uploads (anything up to 100 MB - the common case) reported
          progress exactly once, after the request returned. The row sat on
          "Pending" at 0% for the whole transfer and then jumped to done.
client-5  the downloads registry recorded total=0, because `upsert` runs before
          the first byte and nothing ever wrote the real size back - so a Resume
          offered after an app restart had no size to show a bar against.
client-6  a download whose initial range probe failed wrote a checkpoint with
          total=0. That can never match a later probe, so the partial was
          discarded and every byte already fetched was thrown away.
client-7  a download that failed before any bytes landed left an orphan
          .fhdownload sidecar next to the user's chosen destination, with no UI
          that ever mentions it again.
client-9  README and config.py both said the refresh token is stored in Windows
          Credential Manager. It never has been - only an API token is
          persisted; a password session lives in memory for the life of the
          process.
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "fileheron_client"


# --- client-4 ----------------------------------------------------------------


def test_a_direct_upload_reports_progress_as_it_sends():
    from fileheron_client.api.uploads import _ProgressReader

    seen: list[tuple[int, int]] = []

    class _Raw:
        def __init__(self):
            self.data = b"x" * 10
            self.pos = 0

        def read(self, size=-1):
            chunk = self.data[self.pos : self.pos + (size if size > 0 else len(self.data))]
            self.pos += len(chunk)
            return chunk

        def seek(self, *a, **k):
            self.pos = 0
            return 0

        def tell(self):
            return self.pos

    reader = _ProgressReader(_Raw(), 10, lambda done, total: seen.append((done, total)))
    while reader.read(4):
        pass

    assert seen == [(4, 10), (8, 10), (10, 10)], seen


def test_a_retry_rewinds_the_counter_with_the_body():
    """httpx re-reads the body on a redirect or retry; without the rewind the
    bar would run past 100%."""
    from fileheron_client.api.uploads import _ProgressReader

    seen: list[int] = []

    class _Raw:
        def __init__(self):
            self.pos = 0

        def read(self, size=-1):
            if self.pos >= 8:
                return b""
            self.pos += 4
            return b"xxxx"

        def seek(self, *a, **k):
            self.pos = 0
            return 0

        def tell(self):
            return self.pos

    reader = _ProgressReader(_Raw(), 8, lambda done, _t: seen.append(done))
    reader.read(4)
    reader.seek(0)
    reader.read(4)
    assert seen == [4, 4], seen


def test_a_failing_progress_callback_cannot_break_the_upload():
    """The callback is a UI update; an exception there must not abort a
    transfer that is otherwise fine."""
    from fileheron_client.api.uploads import _ProgressReader

    class _Raw:
        def __init__(self):
            self.done = False

        def read(self, size=-1):
            if self.done:
                return b""
            self.done = True
            return b"abc"

        def tell(self):
            return 3

    def _boom(_d, _t):
        raise RuntimeError("widget destroyed")

    reader = _ProgressReader(_Raw(), 3, _boom)
    assert reader.read(3) == b"abc"


def test_the_upload_path_wraps_the_file():
    from fileheron_client.api import uploads

    src = inspect.getsource(uploads.upload_direct)
    assert "_ProgressReader(raw, size, on_progress)" in src


# --- client-3 ----------------------------------------------------------------


def _sign_out_source() -> str:
    """Source of `_on_sign_out`, read from the FILE.

    Not via `inspect.getsource` on an imported module: importing
    `fileheron_client.ui` pulls in CustomTkinter and therefore tkinter, which CI
    runners do not have - the reason every UI test here is structural (see
    conftest)."""
    src = (SRC / "ui" / "settings_dialog.py").read_text(encoding="utf-8")
    start = src.index("def _on_sign_out(")
    rest = src[start:]
    nxt = rest.find("\n    def ", 1)
    return rest if nxt == -1 else rest[:nxt]


def test_sign_out_does_not_block_the_tk_thread():
    src = _sign_out_source()
    assert "run_in_background" in src, "logout still runs on the Tk main thread"
    assert "api_pkg.logout(api)" in src


def test_the_local_credentials_are_cleared_regardless_of_the_server():
    """A laptop that has left the office must still be able to sign out."""
    src = _sign_out_source()
    # The API token is the only secret ever persisted (config.py); the dead
    # clear_secret("refresh") that used to sit beside it is gone.
    clear_at = src.index('clear_secret("api_token"')
    bg_at = src.index("run_in_background")
    assert bg_at < clear_at
    # ...and no `.join()` / result wait between them.
    assert ".join()" not in src


def test_no_other_blocking_api_call_remains_on_the_tk_thread():
    """Every HTTP call in this overlay must go through the background helper.

    Generic over `api_pkg.<anything>(...)`, not a list of two names: the first
    version knew only `logout` and `get_current_api_token`, so `patch_locale`
    - called inline from the language picker, freezing the window for a full
    round-trip - passed it for a year. A call counts as off-thread when it sits
    inside a lambda or a function nested in a method (the shape every
    `run_in_background` call site here has).
    """
    source = (SRC / "ui" / "settings_dialog.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def _scopes(node: ast.AST) -> list[ast.AST]:
        out = []
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                out.append(cur)
            cur = parents.get(cur)
        return out

    seen: set[str] = set()
    on_thread: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "api_pkg"
        ):
            continue
        seen.add(func.attr)
        scopes = _scopes(node)
        # A lambda, or a def nested inside another def (the method).
        nested = any(isinstance(s, ast.Lambda) for s in scopes) or len(
            [s for s in scopes if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
        ) >= 2
        if not nested:
            on_thread.append((func.attr, node.lineno))
    assert {"logout", "get_current_api_token", "patch_locale"} <= seen, (
        f"the calls vanished; this guard would be vacuous (saw {sorted(seen)})"
    )
    assert not on_thread, f"HTTP calls still run on the Tk main thread: {on_thread}"


# --- client-5 ----------------------------------------------------------------


@pytest.fixture
def registry(tmp_path, monkeypatch):
    from fileheron_client import downloads_registry as dlreg

    monkeypatch.setattr(dlreg, "_registry_path", lambda: tmp_path / "downloads.json")
    return dlreg


def test_the_registry_learns_the_total_once_it_is_known(registry):
    registry.upsert("f1", dest="/tmp/a.bin", filename="a.bin", total=0)
    assert registry.get("f1")["total"] == 0

    registry.set_total("f1", 4096)
    assert registry.get("f1")["total"] == 4096


def test_a_zero_total_never_overwrites_a_real_one(registry):
    """The progress tick fires with total=0 when the server sent no length."""
    registry.upsert("f1", dest="/tmp/a.bin", filename="a.bin", total=4096)
    registry.set_total("f1", 0)
    assert registry.get("f1")["total"] == 4096


def test_setting_the_total_for_an_unknown_file_is_a_no_op(registry):
    registry.set_total("nope", 10)
    assert registry.get("nope") is None


def test_the_download_view_records_it():
    src = (SRC / "ui" / "share_detail_view.py").read_text(encoding="utf-8")
    assert "dlreg.set_total(file_id, total)" in src


# --- client-6 / client-7 -----------------------------------------------------


def test_the_checkpoint_is_written_after_the_size_is_known():
    """Written before the request, `total` is whatever the probe produced -
    None when the probe failed - and a checkpoint recording 0 can never match a
    later resume."""
    from fileheron_client.api import download_resumable

    src = (SRC / "api" / "download_resumable.py").read_text(encoding="utf-8")
    run_single = src[src.index("def _run_single"):src.index("def _run_segmented")]
    write_at = run_single.index("ckpt.write(")
    stream_at = run_single.index("api._http.stream(")
    assert stream_at < write_at, (
        "the checkpoint is still written before the response tells us the size"
    )
    assert download_resumable is not None


def test_a_failed_download_with_no_bytes_leaves_no_sidecar():
    from fileheron_client.api import download_resumable

    src = (SRC / "api" / "download_resumable.py").read_text(encoding="utf-8")
    run_single = src[src.index("def _run_single"):src.index("def _run_segmented")]
    assert "except Exception:" in run_single
    tail = run_single[run_single.index("except Exception:"):]
    assert "ckpt.discard(dest)" in tail
    assert "part.stat().st_size == 0" in tail
    assert download_resumable is not None


def test_a_pause_still_keeps_the_partial():
    """Control: the whole point of the checkpoint is that Pause is resumable."""
    src = (SRC / "api" / "download_resumable.py").read_text(encoding="utf-8")
    run_single = src[src.index("def _run_single"):src.index("def _run_segmented")]
    paused = run_single[run_single.index("except DownloadPaused:"):]
    first = paused[: paused.index("except DownloadCancelled:")]
    assert "discard" not in first, "a paused download now discards its own partial"


# --- client-9 ----------------------------------------------------------------


def test_only_an_api_token_is_ever_persisted():
    """The refresh token has never been written to the keyring; both docs said
    it was."""
    blob = "\n".join(
        p.read_text(encoding="utf-8")
        for p in SRC.rglob("*.py")
    )
    assert 'set_secret("refresh"' not in blob
    assert 'set_secret("api_token"' in blob


@pytest.mark.parametrize(
    "path", ["config.py", "../../README.md"]
)
def test_the_docs_do_not_claim_the_refresh_token_is_stored(path):
    target = (SRC / path).resolve() if not path.startswith("..") else (
        Path(__file__).resolve().parents[1] / "README.md"
    )
    text = target.read_text(encoding="utf-8")
    lowered = text.lower()
    if "credential manager" in lowered or "keyring" in lowered:
        # Any mention must not attach the refresh token to it.
        for line_no, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if "refresh" in low and ("keyring" in low or "credential manager" in low):
                assert "never" in low or "not" in low or "memory" in low, (
                    f"{target.name}:{line_no} still says the refresh token is stored"
                )


def test_the_client_test_count_in_the_readme_is_real():
    """It said "~15 unit tests" for a suite ten times that size, in the file a
    contributor reads to decide whether the suite is worth trusting."""
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
        encoding="utf-8"
    )
    actual = sum(
        len([n for n in ast.walk(ast.parse(f.read_text(encoding="utf-8")))
             if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")])
        for f in Path(__file__).resolve().parent.glob("test_*.py")
    )
    assert actual > 100
    # A number in the README goes stale on the next test added, so assert the
    # ORDER OF MAGNITUDE is honest rather than pinning an exact count. "~15"
    # for a 160-test suite is what this exists to catch.
    import re

    claimed = re.search(r"pytest\s+#\s*~?(\d+)", readme) or re.search(
        r"(\d+)\s+tests", readme
    )
    assert claimed, "the README no longer states a test count at all"
    stated = int(claimed.group(1))
    assert stated >= actual * 0.5, (
        f"README claims {stated} tests; there are {actual}"
    )


def test_the_conftest_names_the_toolkit_the_client_actually_uses():
    """It named PySide6 - a framework this client has never shipped - in the
    file that explains WHY the UI tests are structural."""
    doc = (Path(__file__).resolve().parent / "conftest.py").read_text(encoding="utf-8")
    header = doc[: doc.index('"""', 3) + 3]
    assert "CustomTkinter" in header or "customtkinter" in header
    if "PySide6" in header:
        assert "never" in header, "PySide6 is still named as a live constraint"


def test_json_registry_stays_parseable(registry):
    """Belt and braces on the file the Resume index lives in."""
    registry.upsert("f1", dest="/tmp/a.bin", filename="a.bin", total=1)
    registry.set_total("f1", 2)
    raw = registry._registry_path().read_text(encoding="utf-8")
    assert json.loads(raw)["f1"]["total"] == 2
