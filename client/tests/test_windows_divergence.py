"""Behaviour that differs between Linux (where these tests used to be the only
thing that ran) and Windows (the only platform this client ships for).

client-v1.3.0 was tagged, failed to build, and burned a version number because
`zoneinfo` raises on Windows and nothing in CI had ever executed the suite
there. These cover the rest of that family. Every one of them is written to run
identically on both platforms - a test that skips on Windows is how the first
one got through.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

import pytest

from fileheron_client import motw
from fileheron_client.api import download_checkpoint as ckpt
from fileheron_client.api.shares import _expiry_to_utc_iso
from fileheron_client.formatters import set_display_timezone
from fileheron_client.safe_path import (
    safe_download_leaf,
    safe_join,
    shorten_to_fit,
)

CLIENT = Path(__file__).resolve().parents[1]
SRC = CLIENT / "src" / "fileheron_client"


def _code(path: Path) -> str:
    """Source with comment lines removed - a comment must never be able to
    satisfy a structural assertion."""
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


# --------------------------------------------------------------------------
# Expiry is interpreted in the instance's zone, not the machine's
# --------------------------------------------------------------------------


@pytest.fixture
def vienna():
    set_display_timezone("Europe/Vienna")
    yield
    set_display_timezone(None)


def test_a_naive_expiry_is_read_in_the_instance_timezone(vienna):
    """The whole point of client-v1.3.x. A wall-clock 17:00 typed against an
    instance on Europe/Vienna is 15:00 UTC in January, wherever the laptop is -
    and the dialog says so, so sending anything else makes the label a lie."""
    assert _expiry_to_utc_iso(datetime(2026, 1, 15, 17, 0)) == (
        "2026-01-15T16:00:00+00:00"
    )


def test_summer_time_is_the_instances_summer_time(vienna):
    """CEST, not CET: the offset has to come from the zone's rules on that
    date, which is what a fixed-offset shortcut would get wrong."""
    assert _expiry_to_utc_iso(datetime(2026, 7, 15, 17, 0)) == (
        "2026-07-15T15:00:00+00:00"
    )


def test_an_aware_expiry_is_left_alone(vienna):
    aware = datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc)
    assert _expiry_to_utc_iso(aware) == "2026-01-15T17:00:00+00:00"


def test_a_pre_epoch_expiry_does_not_raise_without_an_instance_zone():
    """`.astimezone()` on a naive pre-1970 value raises OSError on Windows and
    answers on glibc. Only the no-instance-zone fallback can reach it, and a
    crash there would be a platform-dependent failure in the create dialog."""
    set_display_timezone(None)
    assert _expiry_to_utc_iso(datetime(1969, 7, 20, 20, 17)).endswith("+00:00")


def test_the_create_panel_builds_its_expiry_in_the_display_zone():
    """The dialog was fixed in client-v1.3.0 and the create panel was not, so
    the two surfaces of one feature disagreed. Structural because building the
    panel needs Tk."""
    code = _code(SRC / "ui" / "upload_panel.py")
    built = re.search(r"chosen = datetime\(([^\n]*)", code)
    assert built, "the expiry datetime is no longer built where this test looks"
    assert "tzinfo=display_timezone()" in built.group(1)


def test_the_create_panel_names_the_zone_it_uses():
    assert "common.timezone_note" in _code(SRC / "ui" / "upload_panel.py")


# --------------------------------------------------------------------------
# Windows filename hardening
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # ':' is the dangerous one - see _WIN_FORBIDDEN.
        ("C:report.pdf", "Creport.pdf"),
        ("D:report.pdf", "Dreport.pdf"),
        ("notes.txt:hidden", "notes.txthidden"),
        ('quote"name.txt', "quotename.txt"),
        ("pipe|name.txt", "pipename.txt"),
        ("star*.txt", "star.txt"),
        ("q?.txt", "q.txt"),
        ("lt<gt>.txt", "ltgt.txt"),
        # Unchanged behaviour, kept honest.
        ("normal.txt", "normal.txt"),
        ("../../etc/passwd", "passwd"),
        ("CON", "_CON"),
    ],
)
def test_forbidden_characters_are_stripped(raw, expected):
    assert safe_download_leaf(raw) == expected


def test_a_drive_relative_name_cannot_collide_with_a_plain_one(tmp_path):
    """`Downloads / "C:report.pdf"` is `Downloads\\report.pdf` on Windows - the
    drive prefix is dropped - so before sanitisation this shared a destination
    with a genuine report.pdf while the de-dup set believed the two names
    differed. Two live download threads then wrote one file."""
    used: set[str] = set()
    first = safe_join(tmp_path, "report.pdf", used)
    second = safe_join(tmp_path, "C:report.pdf", used)
    assert first != second

    base = PureWindowsPath(r"C:\Users\me\Downloads")
    assert (base / first.name) != (base / second.name)


def test_a_cross_drive_name_stays_inside_the_chosen_folder(tmp_path):
    """`Downloads / "D:x"` leaves the folder entirely on Windows. safe_join
    could only answer that by raising, which took the rest of the batch with
    it; sanitising it to a leaf means it just lands where the user asked."""
    dest = safe_join(tmp_path, "D:payload.exe", set())
    assert dest.parent == tmp_path

    joined = PureWindowsPath(r"C:\Users\me\Downloads") / dest.name
    assert joined.drive == "C:"


def test_an_alternate_data_stream_name_becomes_a_visible_file(tmp_path):
    """`x.txt:evil` writes into a stream of x.txt that no file manager shows -
    the download reports success and the user finds nothing."""
    dest = safe_join(tmp_path, "invoice.pdf:payload", set())
    assert ":" not in dest.name
    dest.write_bytes(b"ok")
    assert [p.name for p in tmp_path.iterdir()] == [dest.name]


def test_a_long_name_is_shortened_to_fit_max_path():
    base = Path("C:/Users/a-fairly-long-account-name/Downloads/2026/July/incoming")
    leaf = shorten_to_fit(base, "x" * 250 + ".pdf")
    assert len(str(base)) + 1 + len(leaf) <= 259
    assert leaf.endswith(".pdf"), "the extension decides the icon and the app"


def test_shortening_leaves_a_name_that_already_fits_alone():
    assert shorten_to_fit(Path("C:/tmp"), "report.pdf") == "report.pdf"


def test_shortening_happens_before_dedup_so_names_stay_unique(tmp_path):
    """Trimming after de-duplication could collide a name back onto one already
    handed out."""
    used: set[str] = set()
    long_name = "y" * 240 + ".pdf"
    a = safe_join(tmp_path, long_name, used)
    b = safe_join(tmp_path, long_name, used)
    assert a != b


# --------------------------------------------------------------------------
# Sharing violations
# --------------------------------------------------------------------------


class _LockedOnce:
    """A callable that raises PermissionError the first n times, like a scanner
    that has the file open for a moment."""

    def __init__(self, n: int) -> None:
        self.left = n
        self.calls = 0

    def __call__(self, *a, **kw) -> None:
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise PermissionError(32, "The process cannot access the file")


def test_replace_retries_a_transient_sharing_violation(tmp_path, monkeypatch):
    """A completed download must not be lost because an on-access scanner held
    the destination open for a fraction of a second."""
    op = _LockedOnce(2)
    monkeypatch.setattr("fileheron_client.api.download_checkpoint.os.replace", op)
    ckpt.replace_with_retry(tmp_path / "a.part", tmp_path / "a")
    assert op.calls == 3


def test_replace_still_raises_when_the_lock_never_clears(tmp_path, monkeypatch):
    """Retrying must not turn a real conflict into a silent success."""
    monkeypatch.setattr(
        "fileheron_client.api.download_checkpoint.os.replace", _LockedOnce(99)
    )
    with pytest.raises(PermissionError):
        ckpt.replace_with_retry(tmp_path / "a.part", tmp_path / "a")


def test_discard_retries_the_locked_partial(tmp_path, monkeypatch):
    calls = _LockedOnce(1)
    monkeypatch.setattr(Path, "unlink", lambda self, missing_ok=False: calls(self))
    ckpt.discard(tmp_path / "download.bin")
    assert calls.calls >= 3, "both sidecars, with a retry on the locked one"


def test_discard_never_raises_into_a_cancel_path(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Path, "unlink", lambda self, missing_ok=False: _LockedOnce(99)(self)
    )
    ckpt.discard(tmp_path / "download.bin")  # must not raise


# --------------------------------------------------------------------------
# Mark-of-the-Web
# --------------------------------------------------------------------------


def test_motw_is_a_no_op_off_windows(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    assert motw.tag_downloaded(tmp_path / "x.bin") is False
    assert list(tmp_path.iterdir()) == []


def test_motw_writes_zone_three_to_the_files_stream(tmp_path, monkeypatch):
    """Zone 3 is 'Internet' - the value that makes SmartScreen warn before an
    unrecognised executable runs and puts Office files in Protected View."""
    monkeypatch.setattr("sys.platform", "win32")
    written = {}

    class _Sink:
        def __init__(self, name):
            self.name = name

        def write(self, body):
            written[self.name] = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "fileheron_client.motw.open",
        lambda name, *a, **kw: _Sink(name),
        raising=False,
    )
    dest = tmp_path / "installer.exe"
    assert motw.tag_downloaded(dest, host_url="https://files.example.com") is True

    stream, body = next(iter(written.items()))
    assert stream == f"{dest}:Zone.Identifier"
    assert "ZoneId=3" in body
    assert "HostUrl=https://files.example.com" in body


def test_motw_failure_never_breaks_a_download(tmp_path, monkeypatch):
    """FAT32 and most network shares have no stream support. A file with no
    mark is exactly what shipped before; a lost download is not."""
    monkeypatch.setattr("sys.platform", "win32")

    def _boom(*a, **kw):
        raise OSError(87, "The parameter is incorrect")

    monkeypatch.setattr("fileheron_client.motw.open", _boom, raising=False)
    assert motw.tag_downloaded(tmp_path / "x.bin") is False


def test_every_completed_download_gets_marked():
    """Both finalize paths, or the mark depends on which transfer mode the
    file happened to take."""
    assert "motw.tag_downloaded" in _code(SRC / "api" / "files.py")
    resumable = _code(SRC / "api" / "download_resumable.py")
    assert "motw.tag_downloaded" in resumable
    assert resumable.count("_finalize(api, part, dest)") == 3


# --------------------------------------------------------------------------
# Shell integration + MIME
# --------------------------------------------------------------------------


def test_explorer_select_is_one_argument():
    """`["explorer", "/select,", path]` puts a space between the switch and the
    path, and Explorer then opens the default folder instead of selecting the
    file. It has to be a single token."""
    code = _code(SRC / "ui" / "share_detail_view.py")
    assert 'f"/select,{path}"' in code
    assert '"/select,",' not in code


def test_mime_detection_does_not_consult_the_windows_registry():
    """`mimetypes.guess_type` merges HKEY_CLASSES_ROOT on Windows, so the type
    recorded server-side - and served to every recipient - depended on what the
    uploader had installed."""
    from fileheron_client.ui import upload_worker

    assert isinstance(upload_worker._MIME, __import__("mimetypes").MimeTypes)
    assert upload_worker.guess_mime("x.pdf") == "application/pdf"
    assert upload_worker.guess_mime("x.unheard-of") == "application/octet-stream"
    code = _code(SRC / "ui" / "upload_worker.py")
    assert "mimetypes.guess_type(" not in code


def test_save_all_isolates_a_rejected_name():
    """safe_join REJECTS by raising, so one unusable name used to abandon every
    file after it - and in a windowed build the traceback goes nowhere."""
    code = _code(SRC / "ui" / "share_detail_view.py")
    body = code.split("def _save_all(")[1].split("\n    def ")[0]
    assert "except (ValueError, OSError):" in body
    assert "continue" in body
    assert "save_all_skipped_body" in body


def test_the_single_file_dialog_gets_a_sanitised_initial_name():
    code = _code(SRC / "ui" / "share_detail_view.py")
    body = code.split("def _download_one(")[1].split("\n    def ")[0]
    assert "initialfile=safe_download_leaf(filename)" in body


# --------------------------------------------------------------------------
# Pre-allocation, trust store, keyring, encoding
# --------------------------------------------------------------------------


def test_preallocation_does_not_zero_fill(tmp_path):
    """`truncate()` goes through the CRT's `_chsize_s` on Windows, which
    physically writes the zeros. On a 30 GB-capable product that means a
    multi-GB download sits at 0% with no network traffic while the disk is
    written once in zeros, then again for real."""
    from fileheron_client.api.download_resumable import _preallocate

    part = tmp_path / "big.part"
    _preallocate(part, 5_000_000)
    assert part.stat().st_size == 5_000_000
    assert part.read_bytes()[:16] == b"\0" * 16

    code = _code(SRC / "api" / "download_resumable.py")
    assert "f.truncate(total)" not in code, "back to the zero-filling form"
    assert "f.seek(total - 1)" in code


def test_preallocation_handles_an_empty_file(tmp_path):
    """seek(-1) would raise; a zero-length remote file must still work."""
    from fileheron_client.api.download_resumable import _preallocate

    part = tmp_path / "empty.part"
    _preallocate(part, 0)
    assert part.stat().st_size == 0


def test_the_http_client_trusts_the_os_certificate_store():
    """certifi alone ignores the Windows Trusted Root store, where an
    organisation's TLS-inspecting proxy puts its CA - so every browser on the
    laptop reaches the instance and this client alone fails at sign-in."""
    import ssl

    from fileheron_client.api.client import _ssl_context

    ctx = _ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED, "verification must stay on"
    assert ctx.check_hostname is True
    # Additive: the OS store AND certifi, so no machine is worse off than it
    # was on the certifi-only default.
    assert len(ctx.get_ca_certs()) > 0
    # And the client has to actually USE it. Asserting only on the context's
    # properties left this test green when the wiring was removed (found by
    # mutating the fix).
    assert "verify=_ssl_context()" in _code(SRC / "api" / "client.py")


def test_the_upload_path_uses_the_same_trust_store():
    """Or a corporate root CA lets the session sign in and then fails every
    upload over 100 MB, which is the least debuggable form of the bug."""
    assert "verify=_ssl_context()" in _code(SRC / "tus.py")


def test_set_secret_reports_whether_it_stored_anything(monkeypatch):
    """Windows Credential Manager is routinely off by enterprise policy. The
    warning went to a logger that is silenced unless diagnostics are on, so the
    token just stopped persisting."""
    from fileheron_client import config

    def _refuse(*a, **kw):
        raise RuntimeError("No recommended backend was available")

    monkeypatch.setattr(config.keyring, "set_password", _refuse)
    assert config.set_secret("api_token", "https://x.example", "fh_tok") is False

    monkeypatch.setattr(config.keyring, "set_password", lambda *a, **kw: None)
    assert config.set_secret("api_token", "https://x.example", "fh_tok") is True


def test_a_failed_store_is_shown_to_the_user():
    code = _code(SRC / "ui" / "login_window.py")
    assert "if not set_secret(" in code
    assert "warn_token_not_stored" in code


def test_keyring_problem_names_a_dead_backend(monkeypatch):
    from fileheron_client import config

    class _Fail:
        pass

    _Fail.__module__ = "keyring.backends.fail"
    monkeypatch.setattr(config.keyring, "get_keyring", lambda: _Fail())
    problem = config.keyring_problem()
    assert problem and "Credential Manager" in problem


def test_the_keyring_note_does_not_fail_the_bundle_check():
    """The self-check's exit code is about the BUNDLE. A CI runner without a
    credential vault must not fail a release for it."""
    src = (SRC / "__main__.py").read_text(encoding="utf-8")
    body = src.split("def _selfcheck(")[1].split("\ndef ")[0]
    assert "selfcheck NOTE: keyring" in body
    assert 'problems.append(f"keyring' not in body


def test_superscript_device_names_are_escaped():
    """Windows maps COM² onto the same device as COM2. `COM².log` is an
    ordinary serial-capture name on Linux."""
    assert safe_download_leaf("COM².log") == "_COM².log"
    assert safe_download_leaf("LPT¹") == "_LPT¹"


def test_the_timezone_label_fallback_is_platform_independent():
    """`%Z` is a short abbreviation on Linux and a long localized name on
    Windows, decoded through the ANSI code page - it can arrive as mojibake and
    it overflows the label it sits in."""
    from fileheron_client.formatters import timezone_label

    set_display_timezone(None)
    label = timezone_label()
    assert re.fullmatch(r"UTC[+-]\d{2}:\d{2}", label), label

    set_display_timezone("Europe/Vienna")
    assert timezone_label() == "Europe/Vienna"
    set_display_timezone(None)


def test_the_tests_read_source_as_utf8_not_the_ansi_code_page():
    """`read_text()` with no encoding decodes as cp1252 on Windows. Two source
    files already carry bytes it cannot decode, so a structural test that reads
    them passes on Linux and raises UnicodeDecodeError on the platform this
    product ships for."""
    # Assembled, or this line is itself the only match.
    needle = ".read_text" + "()"
    offenders = []
    for test_file in (Path(__file__).parent).glob("test_*.py"):
        for i, line in enumerate(
            test_file.read_text(encoding="utf-8").splitlines(), 1
        ):
            if needle in line:
                offenders.append(f"{test_file.name}:{i}")
    assert not offenders, f"bare read_text() decodes as cp1252 on Windows: {offenders}"


def test_the_source_files_that_break_cp1252_are_known():
    """Not a rule against non-ASCII - a guard that the reason the rule above
    exists is real. If this ever finds nothing, the encoding test above still
    stands on its own, but the failure mode it prevents has gone away."""
    undecodable = []
    for path in SRC.rglob("*.py"):
        try:
            path.read_bytes().decode("cp1252")
        except UnicodeDecodeError:
            undecodable.append(path.name)
    assert undecodable, (
        "no source file breaks cp1252 any more - the encoding guard is now "
        "belt-and-braces rather than load-bearing"
    )


def test_every_module_compiles_and_names_resolve():
    """A structural assertion cannot see an undefined NAME.

    `initialfile=safe_download_leaf(filename)` satisfied its own structural
    test while `safe_download_leaf` was never imported, so every single-file
    Download would have raised NameError - found by ruff, which had never been
    pointed at this package. Compiling proves syntax; the symbol check below
    proves the names a module references at module scope exist.
    """
    import ast
    import builtins

    problems = []
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        # Implicit module globals, plus every builtin.
        defined = set(dir(builtins)) | {
            "__file__", "__name__", "__doc__", "__package__", "__spec__",
            "__loader__", "__builtins__", "__debug__",
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    defined.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                defined.add(node.id)
            elif isinstance(node, ast.arg):
                defined.add(node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                defined.add(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                defined.update(node.names)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id not in defined
            ):
                problems.append(f"{path.name}:{node.lineno} uses {node.id!r}")

    assert not problems, "names referenced but never bound: " + "; ".join(problems)


@pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="needs a real Tk; the Linux CI image has none (that is the whole point)",
)
def test_every_ui_module_imports_on_windows():
    """The suite is AST/structural because Linux CI has no tkinter - but the
    WINDOWS runner has Tcl/Tk, and that premise was carried over to it
    unexamined, so the one leg that could import the UI never did.

    Importing is a real gate the structural tests cannot be: it executes every
    module-level statement, so a missing import, a typo in a decorator or a bad
    default argument fails here instead of on a user's desktop. It is also the
    closest thing to launching the app that CI can do without a window.
    """
    import importlib

    failures = []
    for path in sorted((SRC / "ui").glob("*.py")):
        if path.name == "__init__.py":
            continue
        module = f"fileheron_client.ui.{path.stem}"
        try:
            importlib.import_module(module)
        except Exception as exc:
            failures.append(f"{module}: {type(exc).__name__}: {exc}")
    assert not failures, "UI modules that do not import: " + "; ".join(failures)
