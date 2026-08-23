"""The middleware stack, pinned as a list.

Most of this order is ALREADY pinned behaviourally, and it is worth being precise
about which part: `test_scan_guard_middleware.py` asserts a blocked 404 carries
`x-request-id` and the security headers, and that its body carries `request_id` -
neither of which exists unless ScanGuard runs INSIDE both header layers. Move it
outside either and those tests fail. That is the load-bearing relationship.

What was genuinely unpinned is `SelectiveGZipMiddleware`'s position:
`test_selective_gzip.py` builds its OWN bare `Starlette()` app with only that one
middleware, so it never touches the real stack at all. Gzip outermost would, for
instance, compress the scan guard's refusal exactly as it compresses a genuine
404 - so even the scan-guard tests would not notice.

One half of the comment in `main.py` describes something that cannot happen:
"OUTSIDE Starlette's ExceptionMiddleware". `Starlette.build_middleware_stack`
appends `ExceptionMiddleware` after EVERY user middleware unconditionally, so no
ordering of `add_middleware` calls can put anything inside it. That is a
framework invariant, not a property of this file, and reordering `main.py` cannot
break it.
"""
from __future__ import annotations

from app.main import app

# Outermost first. `add_middleware` PREPENDS, so this is the reverse of the
# registration order in main.py.
_EXPECTED = [
    "SecurityHeadersMiddleware",
    "RequestIdMiddleware",
    "ScanGuardMiddleware",
    "SelectiveGZipMiddleware",
]


def _stack() -> list[str]:
    return [m.cls.__name__ for m in app.user_middleware]


def test_the_middleware_stack_is_exactly_this():
    assert _stack() == _EXPECTED, (
        "the middleware order changed. ScanGuard must stay INSIDE "
        "SecurityHeaders and RequestId so its refusal is byte-identical to a "
        "real 404 (a difference is an oracle a scanner can binary-search), and "
        "SelectiveGZip must stay innermost so it does not compress that refusal "
        "differently from a genuine one."
    )


def test_the_scan_guard_is_inside_both_header_layers():
    """Stated as the relationship rather than the list, so a future insertion
    somewhere harmless does not read as this invariant breaking."""
    stack = _stack()
    assert stack.index("SecurityHeadersMiddleware") < stack.index("ScanGuardMiddleware")
    assert stack.index("RequestIdMiddleware") < stack.index("ScanGuardMiddleware")


def test_gzip_is_innermost():
    """The slot nothing else covers - `test_selective_gzip.py` mounts gzip on a
    throwaway app, so it cannot see where the real one sits."""
    assert _stack()[-1] == "SelectiveGZipMiddleware"


def test_the_scan_guard_sits_outside_the_exception_middleware_by_construction():
    """Not an ordering property: Starlette appends ExceptionMiddleware after all
    user middleware, so EVERY user middleware is outside it. Pinned as a fact
    about the framework so the claim in main.py's comment stays checkable across
    a Starlette bump - if this ever fails, the refusal starts reaching the error
    handlers and a blocked source begins writing error_log rows."""
    import inspect

    from starlette.applications import Starlette

    src = inspect.getsource(Starlette.build_middleware_stack)
    assert "ExceptionMiddleware" in src
    idx_user = src.index("self.user_middleware")
    idx_exc = src.index("ExceptionMiddleware", idx_user)
    assert idx_exc > idx_user, (
        "Starlette no longer appends ExceptionMiddleware after the user "
        "middleware; the scan guard's short-circuit may now reach the error "
        "handlers"
    )
