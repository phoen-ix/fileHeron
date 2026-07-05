# Security Policy

file:Heron is a self-hosted file-sharing platform that people run to move
sensitive files. We take security reports seriously and appreciate responsible
disclosure.

## Supported versions

Security fixes are published for the **latest released version** (see the
[Releases](https://github.com/phoen-ix/fileHeron/releases) page) on both tracks:

- **Server** - the newest `vX.Y.Z` tag / GHCR image.
- **Desktop client** - the newest `client-vX.Y.Z` release.

Older releases do not receive backported fixes. Operators should track the
latest release; the server offers an in-app Update for this.

## Reporting a vulnerability

**Please do not open a public issue for a security vulnerability.**

Report privately through GitHub's **[Report a vulnerability](https://github.com/phoen-ix/fileHeron/security/advisories/new)**
button (repo **Security** tab -> **Report a vulnerability**). This opens a private
advisory visible only to you and the maintainers.

Please include, as far as you can:

- affected component (server API, SPA, tusd hook path, desktop client, deployment/compose) and version/tag,
- a clear description and impact,
- reproduction steps or a proof of concept,
- any suggested remediation.

## What to expect

- **Acknowledgement** within 3 business days.
- An initial **assessment** (severity + whether it's confirmed) within 7 business days.
- We aim to ship a fix for a confirmed high-severity issue in the next release,
  and to credit you in the advisory / release notes unless you prefer to stay anonymous.

## Scope

In scope: the server (FastAPI backend, worker, SPA), the tusd upload path and its
HMAC hook boundary, authentication/2FA/OIDC/WebAuthn, API-token scopes, public
links, the config-backup and update/rollback flows, and the desktop client.

Out of scope: issues that require a pre-compromised host or a malicious
administrator; findings against a deployment that has not followed the hardening
guidance in the README (e.g. running with `ENVIRONMENT=development`, exposed
backend/tusd ports, or a misconfigured reverse proxy); and denial-of-service via
sheer volume. Self-hosted operators own their own TLS, host, and network posture.

## Safe harbour

We will not pursue or support legal action against researchers who act in good
faith, avoid privacy violations and service disruption, and give us reasonable
time to remediate before any public disclosure.
