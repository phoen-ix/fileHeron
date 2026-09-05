"""The desktop client's Pydantic mirrors must agree with the backend schemas.

`client/src/fileheron_client/models.py` re-declares the subset of each response
the client reads, with `extra="ignore"` so server-side additions are harmless.
Two kinds of drift ARE harmful and nothing pinned either:

- a field the client declares that the backend never sends: every read of it
  silently gets the client-side default, so a feature quietly stops working
  (the 2026-09 audit found `DirectUploadResponse.sha256_hex` REQUIRED on the
  client while the backend types it `str | None` - a successful upload's reply
  would have been rejected as "upload failed" after the bytes had landed);
- a field the client REQUIRES that the backend may send as null, which fails
  validation at exactly the moment the server answered fine.

`test_frontend_api_types.py` does this for the SPA; this is the client half.
AST-based, so it reads the client without importing it (the client is a
separate package with its own interpreter).
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[2]
_CLIENT_MODELS = _REPO / "client" / "src" / "fileheron_client" / "models.py"
_SCHEMAS = _REPO / "backend" / "app" / "schemas"

pytestmark = pytest.mark.skipif(
    not _CLIENT_MODELS.is_file(), reason="client/ is not present in this checkout"
)

# Client class -> backend class, where the names differ. Every other client
# class must exist under the SAME name in app/schemas.
_ALIASES = {
    "GroupItem": "GroupResponse",
}


def _fields(cls: ast.ClassDef) -> dict[str, tuple[bool, str]]:
    """name -> (required, annotation source)."""
    out: dict[str, tuple[bool, str]] = {}
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = (node.value is None, ast.unparse(node.annotation))
    return out


def _classes(path: pathlib.Path) -> dict[str, ast.ClassDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}


def _backend_classes() -> dict[str, tuple[pathlib.Path, dict[str, tuple[bool, str]]]]:
    out: dict[str, tuple[pathlib.Path, dict[str, tuple[bool, str]]]] = {}
    for path in sorted(_SCHEMAS.glob("*.py")):
        local = _classes(path)
        for name, node in local.items():
            fields = _fields(node)
            # Single-level inheritance inside the same module (GroupDetailResponse
            # extends GroupResponse, ...).
            for base in node.bases:
                parent = local.get(ast.unparse(base))
                if parent is not None:
                    for k, v in _fields(parent).items():
                        fields.setdefault(k, v)
            out.setdefault(name, (path, fields))
    return out


def _client_classes() -> dict[str, dict[str, tuple[bool, str]]]:
    return {
        name: _fields(node)
        for name, node in _classes(_CLIENT_MODELS).items()
        if not name.startswith("_")
    }


def test_the_scan_is_not_vacuous():
    client = _client_classes()
    assert len(client) >= 10
    assert sum(len(f) for f in client.values()) >= 40


def test_every_client_model_has_a_backend_schema():
    backend = _backend_classes()
    missing = [
        name for name in _client_classes()
        if _ALIASES.get(name, name) not in backend
    ]
    assert not missing, (
        f"no backend schema class for {missing}; add the pair to _ALIASES if "
        "the names legitimately differ"
    )


def test_every_alias_still_points_at_a_real_class():
    backend = _backend_classes()
    client = _client_classes()
    for src, dst in _ALIASES.items():
        assert src in client, f"alias source {src} is no longer a client model"
        assert dst in backend, f"alias target {dst} is no longer a backend schema"


def test_the_client_declares_no_field_the_backend_does_not_send():
    backend = _backend_classes()
    drift: list[str] = []
    for name, cfields in _client_classes().items():
        _path, bfields = backend[_ALIASES.get(name, name)]
        for field in sorted(set(cfields) - set(bfields)):
            drift.append(f"{name}.{field}")
    assert not drift, (
        "the desktop client reads fields the backend never sends, so they "
        f"silently take the client-side default: {drift}"
    )


def test_a_field_the_backend_may_null_is_optional_on_the_client():
    backend = _backend_classes()
    drift: list[str] = []
    for name, cfields in _client_classes().items():
        _path, bfields = backend[_ALIASES.get(name, name)]
        for field, (required, ann) in cfields.items():
            if not required or field not in bfields:
                continue
            _breq, bann = bfields[field]
            if "None" in bann and "None" not in ann:
                drift.append(f"{name}.{field}: client {ann!r}, backend {bann!r}")
    assert not drift, (
        "a null from the server would fail client-side validation for: "
        f"{drift}"
    )
