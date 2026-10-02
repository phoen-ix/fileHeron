"""Structural rules for the Secrets tab (server v2.24.0).

The Linux leg has no Tk, so these read the UI modules as source. Each test pins
one rule the views exist to keep:

- no HTTP call on the Tk thread, in ANY ui module;
- nothing in the secrets code logs or prints at all (a log line is where a
  secret's text would outlive the screen);
- the mask is a constant, never the text's length;
- the revealed text is dropped when the card or the view goes;
- the reveal card stays after the last view;
- the tab exists only when the server says secrets are on;
- every locale key the code names exists in both languages, with the same
  placeholders.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "fileheron_client"
UI = SRC / "ui"

SECRET_MODULES = [
    SRC / "secret_rules.py",
    SRC / "secret_format.py",
    SRC / "api" / "secrets.py",
    SRC / "api" / "secret_requests.py",
    UI / "secret_widgets.py",
    UI / "secrets_panel.py",
    UI / "secret_detail_view.py",
    UI / "secret_compose_view.py",
    UI / "secret_request_compose_view.py",
    UI / "secret_request_detail_view.py",
]


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _method(cls: ast.ClassDef, name: str) -> ast.FunctionDef:
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{cls.name}.{name} not found")


def _locale(lang: str) -> dict:
    with (SRC / "locales" / f"{lang}.json").open(encoding="utf-8") as f:
        return json.load(f)


# ---- threading -----------------------------------------------------------------------


def test_no_ui_module_calls_the_api_on_the_tk_thread():
    """Generic over EVERY ui module and every `api_pkg.<x>(...)` call, the
    settings-dialog rule extended: a call counts as off-thread when it sits in
    a lambda or in a function nested inside a method (the shape every
    `run_in_background` call site has)."""
    seen: set[str] = set()
    on_thread: list[str] = []
    for path in sorted(UI.glob("*.py")):
        tree = _tree(path)
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id == "api_pkg"):
                continue
            seen.add(func.attr)
            scopes = []
            cur = parents.get(node)
            while cur is not None:
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    scopes.append(cur)
                cur = parents.get(cur)
            nested = any(isinstance(s, ast.Lambda) for s in scopes) or len(
                [s for s in scopes if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
            ) >= 2
            if not nested:
                on_thread.append(f"{path.name}:{node.lineno} {func.attr}")
    assert {"reveal_secret", "create_secret", "answer_secret_request", "list_secrets",
            "list_shares", "patch_locale"} <= seen, f"the scan went vacuous: {sorted(seen)}"
    assert not on_thread, f"HTTP calls on the Tk main thread: {on_thread}"


# ---- no logging ----------------------------------------------------------------------


def test_the_secrets_code_neither_logs_nor_prints():
    offenders = []
    for path in SECRET_MODULES:
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                if "logging" in names or (isinstance(node, ast.ImportFrom) and node.module == "logging"):
                    offenders.append(f"{path.name}:{node.lineno} imports logging")
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id == "print":
                    offenders.append(f"{path.name}:{node.lineno} print()")
                if isinstance(f, ast.Attribute) and f.attr in {
                    "debug", "info", "warning", "error", "exception", "critical", "log",
                }:
                    offenders.append(f"{path.name}:{node.lineno} .{f.attr}()")
    assert not offenders, offenders


# ---- the reveal card -------------------------------------------------------------------


def test_the_mask_is_a_constant_never_the_texts_length():
    tree = _tree(UI / "secret_widgets.py")
    mask = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "MASK" for t in n.targets))
    assert not [n for n in ast.walk(mask.value) if isinstance(n, ast.Name)], (
        "MASK must be built from literals only"
    )
    value = mask.value
    if isinstance(value, ast.BinOp):
        assert isinstance(value.op, ast.Mult)
        size = len(value.left.value) * value.right.value
    else:
        size = len(value.value)
    assert size >= 8
    card = _class(tree, "RevealCard")
    for fn in card.body:
        if isinstance(fn, ast.FunctionDef):
            calls = [n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call)
                     and isinstance(n.func, ast.Name)]
            assert "len" not in calls, f"RevealCard.{fn.name} measures something"
    paint = ast.unparse(_method(card, "_paint"))
    assert "MASK" in paint and "self._secret_text if shown else MASK" in paint


def test_the_card_drops_the_text_when_it_goes():
    card = _class(_tree(UI / "secret_widgets.py"), "RevealCard")
    clear = ast.unparse(_method(card, "clear"))
    assert "self._secret_text = None" in clear
    destroy = _method(card, "destroy").body
    first = ast.unparse(destroy[0])
    assert first == "self.clear()", "clear() must run before the widget is torn down"
    view = _class(_tree(UI / "secret_detail_view.py"), "SecretDetailView")
    assert "self._card.clear()" in ast.unparse(_method(view, "_leave"))
    assert "self._card.clear()" in ast.unparse(_method(view, "destroy"))


def test_the_card_stays_after_the_last_view():
    """The reload after the last view says `can_reveal: false`; the card the
    user just spent their view on must not follow it off the screen."""
    view = _class(_tree(UI / "secret_detail_view.py"), "SecretDetailView")
    render = ast.unparse(_method(view, "_render"))
    assert "self._show(self._card_box, self._revealed_here)" in render
    reveal = ast.unparse(_method(view, "_reveal"))
    assert "self._revealed_here = True" in reveal
    # ...and only this view's own reveal sets it.
    setters = [n for n in ast.walk(view) if isinstance(n, ast.Assign)
               and any(ast.unparse(t) == "self._revealed_here" for t in n.targets)]
    assert len(setters) == 2  # False in __init__, True after a reveal


def test_the_view_sends_both_passphrases():
    view = _class(_tree(UI / "secret_detail_view.py"), "SecretDetailView")
    reveal = _method(view, "_reveal")
    call = next(n for n in ast.walk(reveal) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "reveal_secret")
    assert {k.arg for k in call.keywords} == {"passphrase", "request_passphrase"}


# ---- forms -----------------------------------------------------------------------------


def test_send_is_derived_from_the_visible_blockers():
    base = _class(_tree(UI / "secret_compose_view.py"), "ComposeBase")
    recheck = ast.unparse(_method(base, "recheck"))
    assert "self._blockers.show_blockers(blockers)" in recheck
    assert "'disabled' if blockers or self._sending else 'normal'" in recheck
    send = ast.unparse(_method(base, "_send"))
    assert "self.blockers()" in send
    for path, cls, rule in (
        (UI / "secret_compose_view.py", "SecretComposeView", "secret_blockers"),
        (UI / "secret_request_compose_view.py", "SecretRequestComposeView", "request_blockers"),
    ):
        assert rule in ast.unparse(_method(_class(_tree(path), cls), "blockers"))


def test_a_client_never_gets_groups_addresses_or_a_link():
    base = ast.unparse(_class(_tree(UI / "secret_compose_view.py"), "ComposeBase"))
    assert "allow_groups=not self._is_client" in base
    assert "self._can_external = bool(me.can_send_secrets_external) and me.role != 'client'" in base


# ---- the tab ---------------------------------------------------------------------------


def test_the_tab_exists_only_when_secrets_are_on():
    tree = _tree(UI / "main_window.py")
    adds = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "add"
            and n.args and ast.unparse(n.args[0]) == "TAB_SECRETS"]
    assert len(adds) == 1
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    cur = parents[adds[0]]
    while not isinstance(cur, ast.If):
        cur = parents[cur]
    assert ast.unparse(cur.test) == "self._me.secrets_enabled"


# ---- locales ---------------------------------------------------------------------------


def _key_constants() -> set[str]:
    out: set[str] = set()
    for path in SECRET_MODULES + [UI / "main_window.py"]:
        for node in ast.walk(_tree(path)):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and re.fullmatch(r"secrets\.[a-z0-9_.]+", node.value)):
                out.add(node.value)
    return out


def _lookup(table: dict, dotted: str):
    node = table
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def test_every_key_the_code_names_exists_in_both_languages():
    """`test_i18n` sees only literal `t("...")` calls; the secrets code also
    keeps keys in tables (presets, scopes, states), so every `secrets.*`
    constant is checked here."""
    keys = _key_constants()
    assert len(keys) > 150, "the scan went vacuous"
    for lang in ("en", "de"):
        table = _locale(lang)
        missing = sorted(k for k in keys if not isinstance(_lookup(table, k), str))
        assert not missing, f"{lang}: {missing}"


def _flatten(node, prefix=""):
    for k, v in node.items():
        if isinstance(v, dict):
            yield from _flatten(v, f"{prefix}{k}.")
        else:
            yield f"{prefix}{k}", v


def test_the_two_languages_carry_the_same_keys_and_placeholders():
    en = dict(_flatten(_locale("en")["secrets"]))
    de = dict(_flatten(_locale("de")["secrets"]))
    assert set(en) == set(de)
    field = re.compile(r"\{(\w+)\}")
    drift = [k for k in en if set(field.findall(en[k])) != set(field.findall(de[k]))]
    assert not drift, drift


def test_every_secret_error_code_is_translated():
    en, de = _locale("en")["errors"], _locale("de")["errors"]
    for code in ("SECRET_PASSPHRASE_INVALID", "SECRET_REQUEST_PASSPHRASE_INVALID",
                 "SECRET_RATE_LIMITED", "SECRET_REQUEST_CLOSED", "SECRETS_DISABLED"):
        assert code in en and code in de
