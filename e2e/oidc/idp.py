"""A stand-in OpenID Connect provider for the e2e stack (docker-compose.e2e.yml).

Authorization-code flow only, enough for file:Heron's SSO sign-in and connect:
discovery, a sign-in form that asks for an email, the token exchange, and the
JWKS. The ID token is RS256, signed with a key made at start-up, and carries
`email_verified: true`. The issuer is the address the backend uses (inside the
compose network); the sign-in form is published to the browser on its own port.
Test-only: it trusts any email typed into the form.
"""

import html
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = os.environ["IDP_ISSUER"].rstrip("/")
PUBLIC = os.environ["IDP_PUBLIC_URL"].rstrip("/")
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key())), "kid": "e2e", "use": "sig", "alg": "RS256"}
CODES: dict[str, dict] = {}

FORM = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>E2E IdP</title></head>
<body><main><h1>E2E identity provider</h1><form method="post" action="/authorize">
<label>Email <input name="email" type="email" required></label>{hidden}
<button type="submit">Sign in at the IdP</button></form></main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict, status: int = 200) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _form(self) -> dict[str, str]:
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        return {k: v[0] for k, v in parse_qs(raw).items()}

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/.well-known/openid-configuration":
            return self._json({
                "issuer": ISSUER,
                "authorization_endpoint": f"{PUBLIC}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
                "response_types_supported": ["code"],
                "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"],
            })
        if url.path == "/jwks":
            return self._json({"keys": [JWK]})
        if url.path == "/authorize":
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            hidden = "".join(
                f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in q.items()
            )
            return self._send(200, FORM.format(hidden=hidden).encode(), "text/html; charset=utf-8")
        self._json({"error": "not_found"}, 404)

    def do_POST(self) -> None:
        f = self._form()
        if self.path == "/authorize":
            code = secrets.token_urlsafe(16)
            CODES[code] = f
            target = f'{f["redirect_uri"]}?{urlencode({"code": code, "state": f["state"]})}'
            return self._send(302, b"", "text/plain", {"Location": target})
        if self.path == "/token":
            req = CODES.pop(f.get("code", ""), None)
            if not req or req["client_id"] != f.get("client_id") or req["redirect_uri"] != f.get("redirect_uri"):
                return self._json({"error": "invalid_grant"}, 400)
            now = int(time.time())
            claims = {
                "iss": ISSUER,
                "sub": f'e2e-{req["email"]}',
                "aud": req["client_id"],
                "iat": now,
                "exp": now + 300,
                "nonce": req.get("nonce"),
                "email": req["email"],
                "email_verified": True,
            }
            token = jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "e2e"})
            return self._json({"access_token": secrets.token_urlsafe(16), "token_type": "Bearer", "id_token": token})
        self._json({"error": "not_found"}, 404)

    def log_message(self, fmt: str, *args) -> None:
        print(f"idp: {fmt % args}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
