# file:Heron v1.52.0

**The new-device sign-in alert now tells you who actually signed in.** The
"sign-in from a new device" security email used to show a *region* like `~bf56e`
and a bare *browser* like `Firefox on Windows`. The region was never a place - it
was a privacy hash of your network, unreadable by design - and the browser line
dropped the version and the raw device string. This release replaces the hash with
the **real IP address** and adds the **browser version** plus the **full
user-agent**, so a sign-in you don't recognise is easy to spot.

## What's new

- **Real IP instead of a hash.** The "region" row is gone. The alert now shows the
  actual client IP of the sign-in (labelled **IP address**), the same IP your audit
  log already records. The old privacy hash still runs quietly behind the scenes to
  decide whether a sign-in is from a *new* device, so how often you get alerted is
  unchanged.
- **Browser version in the summary.** "Firefox on Windows" becomes
  "**Firefox 128 on Windows**" - Chrome, Edge, Firefox and Safari all report their
  major version. The OS version is intentionally left off because modern browsers
  freeze or spoof it (every Windows 10 and 11 machine reports the same value).
- **Full user-agent line.** A new **user agent** row carries the complete raw
  header for the rare case you need to inspect the exact client. It only appears
  when the header is present, and it is HTML-escaped, so a crafted device string
  can never inject markup into the email.
- **English and German, text and HTML.** All four templates updated; long
  user-agent strings wrap cleanly in every mail client.

## Upgrade notes

- Rolls forward via **Update** in `/admin/system`.
- **No database migration and no host step** - this is an email payload + template
  change only.
- If you customised the **login-alert** email under
  `/admin/settings/email-templates`, your template keeps working: the `[IP]` token
  now resolves to the real IP, and a new `[USER_AGENT]` token is available to drop
  into your layout whenever you like.
- The lockout-warning email is untouched and still shows its privacy IP hint.
- Rolling back to a pre-v1.52 image is safe.

## Container images

- `ghcr.io/phoen-ix/fileheron-backend:v1.52.0`
- `ghcr.io/phoen-ix/fileheron-worker:v1.52.0`
- `ghcr.io/phoen-ix/fileheron-frontend:v1.52.0`
- `ghcr.io/phoen-ix/fileheron-updater-shim:v1.52.0`
- `ghcr.io/phoen-ix/fileheron-updater-executor:v1.52.0`

Click **Update** in `/admin/system` to roll forward.
