"""Two layout rules for every table in the SPA, checked over EVERY .vue file
rather than a list of today's tables, so a new view is held to them too.

1. Each <table> is the only child of a `.fh-table-scroll` wrapper, which
   scrolls sideways. Without it a table wider than a phone widened the whole
   PAGE: the header, the filters and every notice scrolled with it (share
   lists, Users, Sessions at 390 px).
2. No <td>/<th> carries a class whose own rule sets display: flex or grid. A
   cell that stops being a table-cell no longer stretches to its row's height,
   so its bottom border floated mid-row (/admin/sessions' Actions, found in six
   cells). The flex layout belongs on a <div> inside the cell.

Both were found by a render pass in a real browser; happy-dom has no layout
engine. They live here rather than in vitest for the reason
test_frontend_a11y_tokens.py gives: vitest serves a stylesheet as an empty
string, and rule 1 is only true if global.css says so.
"""
from __future__ import annotations

import pathlib
import re

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"
VUE = sorted(FRONTEND.rglob("*.vue"))
GLOBAL_CSS = (FRONTEND / "styles" / "global.css").read_text()

# An opening tag's attributes may hold a quoted `>` - `v-else-if="items.length
# > 0"` sits on five of these wrappers - so an attribute run is "not > or a
# quote, or a whole quoted string", never a bare [^>]*.
_ATTRS = r'(?:[^>"]|"[^"]*")*'
_WRAPPER_BEFORE = re.compile(rf"<div\b({_ATTRS})>\s*\Z")
_CELL_CLASS = re.compile(rf'<t[dh]\b{_ATTRS}?\sclass="([^"]+)"')


def _unwrapped_tables(src: str) -> int:
    bad = 0
    for m in re.finditer(r"<table\b", src):
        before = _WRAPPER_BEFORE.search(src[: m.start()])
        close = src.find("</table>", m.start())
        after = "" if close == -1 else src[close + len("</table>") :]
        wrapped = (
            before is not None
            and re.search(r'\sclass="fh-table-scroll"', before.group(1)) is not None
            and re.match(r"\s*</div>", after) is not None
        )
        bad += not wrapped
    return bad


def _flex_cells(src: str) -> list[str]:
    """Classes on a <td>/<th> whose own rule (`.x {` or `td.x {`, not a
    descendant selector like `.x .y {`) makes the cell a flex or grid box."""
    at = src.find("<style")
    style = "" if at == -1 else src[at:]
    found = []
    for m in _CELL_CLASS.finditer(src):
        for cls in m.group(1).split():
            rule = re.compile(rf"(?:^|[\s,}}])(?:t[dh])?\.{re.escape(cls)}\s*\{{([^}}]*)\}}")
            for r in rule.finditer(style):
                if re.search(r"display:\s*(?:inline-)?(?:flex|grid)\b", r.group(1)):
                    found.append(cls)
    return found


def test_every_table_is_the_only_child_of_a_scroll_wrapper():
    offenders = [str(p.relative_to(FRONTEND)) for p in VUE if _unwrapped_tables(p.read_text())]
    assert offenders == []
    total = sum(len(re.findall(r"<table\b", p.read_text())) for p in VUE)
    assert total >= 20, f"scanned only {total} tables - is the glob still finding the views?"


def test_the_wrapper_scrolls_sideways():
    assert re.search(r"\.fh-table-scroll\s*\{[^}]*overflow-x:\s*auto", GLOBAL_CSS)


def test_no_table_cell_is_itself_a_flex_or_grid_box():
    offenders = [
        f"{p.relative_to(FRONTEND)}: .{cls}" for p in VUE for cls in _flex_cells(p.read_text())
    ]
    assert offenders == []
    cells = sum(len(_CELL_CLASS.findall(p.read_text())) for p in VUE)
    assert cells >= 20, f"scanned only {cells} classed cells"


def test_the_scans_catch_what_they_are_for():
    assert _unwrapped_tables('<div class="panel"><table></table></div>') == 1
    assert _unwrapped_tables('<div class="fh-table-scroll"><table></table><p>x</p></div>') == 1
    assert _unwrapped_tables('<div v-else class="fh-table-scroll">\n  <table>\n  </table>\n</div>') == 0
    assert _unwrapped_tables('<div v-else-if="n > 0" class="fh-table-scroll"><table></table></div>') == 0

    def cell(rule: str) -> str:
        return f'<td class="actions"></td><style>\n{rule}\n</style>'

    assert _flex_cells(cell(".actions {\n  display: flex;\n}")) == ["actions"]
    assert _flex_cells(cell("td.actions { display: grid; }")) == ["actions"]
    assert _flex_cells(cell(".actions .btn {\n  display: flex;\n}")) == []
    assert _flex_cells(cell(".actions {\n  white-space: nowrap;\n}")) == []
    bound = '<td :class="{ a: n > 0 }" class="actions"></td><style>\n.actions { display: flex; }\n</style>'
    assert _flex_cells(bound) == ["actions"]
