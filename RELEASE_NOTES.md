# file:Heron v1.19.0

**Outbound webhooks.** file:Heron can now send a **signed HTTP POST** to a URL you
choose whenever something happens — a share is created, a file is downloaded, a
file is quarantined, or an operational alert fires. It's the integration unlock:
wire events into your automation, an audit collector, or a chat channel.

## What's new

- **Admin → Settings → Webhooks** — register an endpoint, pick which events it
  receives, and you're done. Each webhook gets its own **signing secret** (shown
  once) so the receiver can verify every request via the `X-Webhook-Signature`
  header (HMAC-SHA256).
- **Slack / Teams without storing credentials.** Point a webhook at a Slack or
  Teams *incoming-webhook* URL and events flow straight into the channel — no
  per-user chat tokens kept anywhere.
- **Subscribable events:** share created · share/file downloaded · share revoked ·
  share expired · file quarantined · public-link consumed · SSO account linked ·
  user erased · **operational alerts** (cron failure, low disk, AV/Redis down).
- **Delivery log + retry.** Every attempt is recorded with its status and response
  code; failed deliveries retry automatically with backoff, and you can re-send any
  delivery by hand from the **Deliveries** panel. A **Test** button fires a sample
  event so you can confirm wiring immediately.

## Good to know

- **Reliable + non-intrusive.** Deliveries run in the background and retry on
  transient failures; a misconfigured or down endpoint **never** affects the
  underlying action (the share, download, or upload always proceeds).
- **Minimal, signed payloads.** A webhook body carries the event name, a
  timestamp, the actor's id, and the event's metadata — **no email addresses, no
  file contents, no tokens**.
- The signing secret is encrypted at rest and never shown again after creation
  (rotate it any time from the webhook's settings). Delivery history is kept for
  30 days (admin-tunable under **Settings → Advanced**).
- Available in English and German. **Admins only.**

## Upgrade notes

- **One small, automatic database migration** adds the `webhooks` and
  `webhook_deliveries` tables. No `.env` change. Just click **Update**.

## Container images

Published to GitHub Container Registry:

- `ghcr.io/phoen-ix/fileheron-backend:v1.19.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.19.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.19.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.19.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.19.0`

Click **Update** in `/admin/system` to roll forward.
