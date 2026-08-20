"""The SPA's hand-written API types must match the OpenAPI schema.

`frontend/src/types/api.ts` (143 interfaces) plus `frontend/src/api/*.ts`
(another 57) are the largest hand-maintained cross-language artefact in this
repo, and until now the only significant one with no pin at all. Six live
divergences had accumulated, one of them for 289 commits and 59 releases:
`NotificationCategory` was missing `server_error`, a category the backend
dispatches and returns a preferences row for. Both places it could have
surfaced in TypeScript are `as`-casts, and every drift found was FIELD-level
inside a correctly-named interface - exactly what `vue-tsc -b` cannot see.

This follows the idiom the repo already uses eleven times over
(`test_wrong_secret_routes.py`, `test_gate_wiring_coverage.py`,
`test_error_code_i18n_coverage.py`): a Python test that reads BOTH sides,
with the declarations kept honest by anti-vacuity assertions rather than by
anyone remembering.

Why a test and not code generation: 47 routes answered `-> dict`, the error
envelope is assembled inside exception handlers where FastAPI's generator
cannot see it, generation would widen twelve deliberately-narrowed unions
back to `string`, and 148 symbols are imported by name across 69 files.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# `/repo` is the container layout; parents[2] the checkout layout. Same
# fallback test_gate_wiring_coverage.py uses.
_CANDIDATES = (Path("/repo"), Path(__file__).resolve().parents[2])
REPO = next((c for c in _CANDIDATES if (c / "frontend" / "src").is_dir()), None)

pytestmark = pytest.mark.skipif(
    REPO is None, reason="frontend/ is not present in this checkout"
)


# --- what the two sides are allowed to disagree about ------------------------

# TS name -> OpenAPI schema name. Each entry is a deliberate rename, not drift.
RENAMED = {
    "SessionRecord": "SessionResponse",
    "ErasePreflight": "ErasePreflightResponse",
    "EmailPlaceholderMeta": "PlaceholderMeta",
    "PublicLinkAllowedUserItem": "AllowedUserItem",
    "PublicLinkAllowedGroupItem": "AllowedGroupItem",
    "ScanGuardSettings": "ScanGuardSettingsResponse",
}

# TS types with no backend model, each with the reason it cannot have one.
FRONTEND_ONLY = {
    "ApiErrorEnvelope": "assembled as a dict inside middleware/errors.py; "
                        "add_exception_handler output never reaches the schema generator",
    "IpBlockListParams": "query-parameter bag, not a body",
    "ListSharesParams": "query-parameter bag, not a body",
    "UpdateScanGuardSettings": "a TS Omit<> over ScanGuardSettings, not a distinct model",
    "AdminNavCollapseMode": "a purely client-side preference vocabulary",
}


# --- a very small TypeScript reader ------------------------------------------

_IFACE = re.compile(r"^export interface (\w+)(?:\s+extends\s+([\w,\s]+))?\s*\{", re.M)
_TYPE_UNION = re.compile(r"^export type (\w+)\s*=\s*((?:[^=;{}]|\n)*?)(?=\n(?:export|/\*|//)|\n\n|\Z)", re.M)
_FIELD = re.compile(r"^\s{2}(\w+)(\??):\s*(.+?)\s*$")


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def _block(src: str, open_idx: int) -> str:
    """Text between the brace at open_idx and its match."""
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_idx + 1:i]
    raise AssertionError("unbalanced braces in the TypeScript source")


def _parse_ts(src: str) -> tuple[dict[str, dict], dict[str, set[str]]]:
    """-> ({interface: {field: optional}}, {type alias: {union members}})"""
    src = _strip_comments(src)
    interfaces: dict[str, dict] = {}
    for m in _IFACE.finditer(src):
        name, bases = m.group(1), m.group(2)
        body = _block(src, src.index("{", m.start()))
        fields: dict[str, bool] = {}
        depth = 0
        for line in body.splitlines():
            if depth == 0:
                fm = _FIELD.match(line)
                if fm:
                    fields[fm.group(1)] = fm.group(2) == "?"
            depth += line.count("{") + line.count("[") - line.count("}") - line.count("]")
            depth = max(depth, 0)
        interfaces[name] = {
            "fields": fields,
            "extends": [b.strip() for b in (bases or "").split(",") if b.strip()],
            "raw": body,
        }

    aliases: dict[str, set[str]] = {}
    for m in _TYPE_UNION.finditer(src):
        members = re.findall(r"'([^']+)'", m.group(2))
        if members and "{" not in m.group(2):
            aliases[m.group(1)] = set(members)
    return interfaces, aliases


def _resolve(name: str, interfaces: dict[str, dict]) -> dict[str, bool]:
    """Field map with `extends` folded in."""
    out: dict[str, bool] = {}
    for base in interfaces[name]["extends"]:
        if base in interfaces:
            out.update(_resolve(base, interfaces))
    out.update(interfaces[name]["fields"])
    return out


# --- fixtures ----------------------------------------------------------------


def _ts_sources() -> dict[str, dict]:
    assert REPO is not None
    src = (REPO / "frontend" / "src" / "types" / "api.ts").read_text(encoding="utf-8")
    for p in sorted((REPO / "frontend" / "src" / "api").glob("*.ts")):
        src += "\n" + p.read_text(encoding="utf-8")
    return {"src": src}


@pytest.fixture(scope="module")
def ts():
    interfaces, aliases = _parse_ts(_ts_sources()["src"])
    # Anti-vacuity: a parser that silently matched nothing would make every
    # assertion below pass. The tree has ~200 interfaces and ~20 aliases.
    assert len(interfaces) > 150, f"the TypeScript scan found only {len(interfaces)} interfaces"
    assert len(aliases) > 10, f"the TypeScript scan found only {len(aliases)} string-union aliases"
    return interfaces, aliases


@pytest.fixture(scope="module")
def openapi():
    from app.main import app

    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    assert len(schemas) > 200, f"the OpenAPI schema has only {len(schemas)} components"
    return spec, schemas


def _response_schema_names(spec: dict) -> set[str]:
    """Schemas reachable as a 2xx response body, transitively.

    Optionality is only meaningful for these: on a REQUEST body, TS `field?:`
    against Pydantic `X | None = None` is the ordinary idiom, not drift.
    """
    schemas = spec["components"]["schemas"]
    seen: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                nm = ref.rsplit("/", 1)[1]
                if nm not in seen:
                    seen.add(nm)
                    walk(schemas.get(nm, {}))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for methods in spec["paths"].values():
        for op in methods.values():
            if not isinstance(op, dict):
                continue
            for code, resp in (op.get("responses") or {}).items():
                if str(code).startswith("2"):
                    walk(resp)
    return seen


# --- the assertions ----------------------------------------------------------


def test_declared_renames_and_exemptions_are_all_real(ts, openapi):
    """A stale entry in either map is a hole: it silences a check forever."""
    interfaces, aliases = ts
    _, schemas = openapi
    known = set(interfaces) | set(aliases)
    for ts_name, schema_name in RENAMED.items():
        assert ts_name in known, f"RENAMED names {ts_name!r}, which no longer exists in the SPA"
        assert schema_name in schemas, f"RENAMED points {ts_name!r} at {schema_name!r}, which is not a schema"
    for ts_name in FRONTEND_ONLY:
        assert ts_name in known, f"FRONTEND_ONLY names {ts_name!r}, which no longer exists in the SPA"
        assert ts_name not in schemas, (
            f"{ts_name!r} is exempted as frontend-only but the backend now has a "
            "schema of that name - drop the exemption so it gets checked"
        )


def test_every_matched_interface_has_the_same_fields(ts, openapi):
    interfaces, _ = ts
    _, schemas = openapi
    problems: list[str] = []
    matched = 0
    for name in sorted(interfaces):
        if name in FRONTEND_ONLY:
            continue
        schema = schemas.get(RENAMED.get(name, name))
        if schema is None or "properties" not in schema:
            continue
        matched += 1
        ts_fields = set(_resolve(name, interfaces))
        py_fields = set(schema["properties"])
        only_ts = ts_fields - py_fields
        only_py = py_fields - ts_fields
        if only_ts:
            problems.append(f"{name}: the SPA declares fields the API does not send: {sorted(only_ts)}")
        if only_py:
            problems.append(f"{name}: the API sends fields the SPA does not declare: {sorted(only_py)}")
    assert matched > 80, f"only {matched} interfaces matched a schema - the pin has stopped working"
    assert not problems, "frontend/backend type drift:\n  " + "\n  ".join(problems)


def test_response_models_agree_on_which_fields_are_optional(ts, openapi):
    """Response-side only. `ImapSettingsResponse` marked two required fields
    optional, and a view compensated with `?? true` / `?? false`."""
    interfaces, _ = ts
    spec, schemas = openapi
    response_names = _response_schema_names(spec)
    assert len(response_names) > 50, f"only {len(response_names)} response schemas found"
    problems: list[str] = []
    checked = 0
    for name in sorted(interfaces):
        if name in FRONTEND_ONLY:
            continue
        schema_name = RENAMED.get(name, name)
        if schema_name not in response_names:
            continue
        schema = schemas.get(schema_name)
        if schema is None or "properties" not in schema:
            continue
        checked += 1
        required = set(schema.get("required", []))
        for field, ts_optional in _resolve(name, interfaces).items():
            if field not in schema["properties"]:
                continue  # reported by the field-set test
            py_required = field in required
            if py_required and ts_optional:
                problems.append(
                    f"{name}.{field}: the API always sends it, the SPA marks it optional"
                )
    assert checked > 20, f"only {checked} response interfaces checked - the pin has stopped working"
    assert not problems, "optionality drift on response models:\n  " + "\n  ".join(problems)


def test_string_union_aliases_match_the_backend_enums(ts, openapi):
    """This is the assertion that catches a missing enum member - the shape of
    the `server_error` drift."""
    _, aliases = ts
    _, schemas = openapi
    problems: list[str] = []
    matched = 0
    for name, members in sorted(aliases.items()):
        if name in FRONTEND_ONLY:
            continue
        schema = schemas.get(RENAMED.get(name, name))
        if schema is None or "enum" not in schema:
            continue
        matched += 1
        py_members = set(schema["enum"])
        if members != py_members:
            problems.append(
                f"{name}: SPA has {sorted(members)}, API has {sorted(py_members)} "
                f"(missing from the SPA: {sorted(py_members - members) or 'none'}; "
                f"unknown to the API: {sorted(members - py_members) or 'none'})"
            )
    assert matched >= 5, f"only {matched} union aliases matched a backend enum"
    assert not problems, "enum drift:\n  " + "\n  ".join(problems)
