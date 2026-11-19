"""No Argon2id on the event loop (audit 2026-11-19).

The backend is ONE uvicorn process. A 64 MiB Argon2id hash or verify inside an
`async def` holds the event loop for ~0.2 s, freezing every download, SSE
stream and /api/health with it. `auth.averify` / `auth.ahash` exist for that,
but four routes still ran the step-up password gate on the loop (passkey
registration, backup import, the SMTP and IMAP test buttons) and four more
hashed a new password there (reset, change, setup, admin user creation).

Pinned generically: no direct call to a synchronous hashing or step-up gate
anywhere in `app/` inside an `async def` body. Hand it to
`run_in_threadpool` / `asyncio.to_thread` (passing the function, not calling it).
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# Every function that hashes on the calling thread, directly or through a gate.
BLOCKING = frozenset({
    "argon2_hash",
    "argon2_verify",
    "password_matches",
    "verify_password_or_403",
    "_verify_password_or_403",
    "guard_and_audit",
    "_step_up_with_second_factor",
    "_match_recovery_hash",
})


def _violations(tree: ast.AST, where: str) -> list[str]:
    found = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        stack = list(fn.body)
        while stack:
            node = stack.pop()
            # A nested def/lambda is not run by the coroutine itself.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                if name in BLOCKING:
                    found.append(f"{where}:{node.lineno} {fn.name}() calls {name}()")
            stack.extend(ast.iter_child_nodes(node))
    return found


def test_the_scanner_catches_a_planted_violation():
    planted = ast.parse(
        "async def route(db, user, pw):\n"
        "    step_up.verify_password_or_403(db, user, pw)\n"
        "    def helper():\n"
        "        argon2_hash(pw)\n"  # nested sync def: not the coroutine's own work
        "    await run_in_threadpool(step_up.verify_password_or_403, db, user, pw)\n"
    )
    assert _violations(planted, "planted") == [
        "planted:2 route() calls verify_password_or_403()"
    ]


def test_no_async_def_hashes_on_the_event_loop():
    files = sorted(APP.rglob("*.py"))
    async_defs = 0
    violations: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        async_defs += sum(isinstance(n, ast.AsyncFunctionDef) for n in ast.walk(tree))
        violations += _violations(tree, str(path.relative_to(APP.parent)))
    assert async_defs > 40, "the scan walked suspiciously few async functions"
    assert violations == [], "\n".join(violations)
