# file:Heron v2.14.1

**A documented command that reconfigured your live server, and three controls
that could not go off.**

A patch release, and like the last one it is mostly about the checks rather than
the product. No migration, no host step, no API change, no default moves, no
desktop-client release beside it. Two things move on the wire, both narrow and
both described below: what a configuration import does after it finishes, and
how the two anonymous telemetry endpoints treat an oversized request.

---

## The contributor guide's end-to-end command reconfigured your live server

`CONTRIBUTING.md` told you to run the browser test suite like this:

```
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d --build
```

That is missing `COMPOSE_PROJECT_NAME=fileheron_e2e`, which the compose file
it names carries in its own header. Docker Compose defaults the project name to
the directory, and for a checkout in `fileHeron/` that resolves to `fileheron`
— **the live project**. So the command does not stand up an isolated test
stack. It recreates your running containers with the test overrides: **antivirus
scanning disabled**, the application in development mode, **secure cookies
off**, and the login rate limit raised to 1000 attempts. Development mode also
switches on the dev account seeding, which creates `user@e2e.local` with a
password published in this repository.

There was also no teardown line, so the natural next step — `docker compose
down` — takes production with it.

**If you have ever run that command from your live checkout:** bring the stack
back up normally (`docker compose up -d`), which restores every one of those
settings, then look in *Admin → Users* for `user@e2e.local` and delete it. It is
an ordinary client account, not an administrator — the administrator bootstrap
refuses to create a second admin on an instance that already has one, so
`admin@e2e.local` is never created here.

The part that does not undo itself is the antivirus. Anything uploaded while
the stack was in that state was recorded as clean **without being scanned**, and
that mark is permanent: there is no rescan action, because the automatic
re-scan only revisits files whose scan never completed, and these have a
completed result. If the window was more than a moment, treat files uploaded
during it as unscanned.

The guide now sets the project name, explains what happens without it, and
gives the matching teardown. A test fails the build if it loses either again.

## Importing a configuration could leave IP blocks enforcing invisibly

Importing a configuration backup writes settings straight into the database. It
does not go through the code path the settings pages use, and that path does
more than write — it replays a set of side effects. Three of them were being
skipped.

**The scan guard's blocked networks.** Each block records the network it covers
as text, computed from the IPv6 grouping prefix that was in force when it was
written. Changing that prefix through the settings page releases the live
network blocks for exactly this reason. An import did not, so a backup carrying
a different prefix left blocks stamped under the old one — and because a block
matches by address containment rather than by that text, the orphaned block
went on refusing service while the *Blocked sources* page had nothing to show
you. Now they are released properly, each with its own audit entry, and the
import summary tells you how many and why.

**A guard that cannot fire.** The settings page refuses to save a scan guard
that is switched on with none of its three detection signals enabled, because
that renders as "on" and can never do anything. A backup can contain precisely
that combination. It is now switched off on import with a warning in the
summary, rather than stored — you can see it is off and turn it on deliberately.

**Single sign-on signing keys.** Provider identities survive an import
unchanged, and the cache of each provider's signing keys is keyed on that
identity alone for an hour. Reusing an identity for a different provider
therefore validated sign-in tokens against the previous provider's keys until
the cache expired. The cache is now cleared when providers are imported.

All three run after the import has committed, so none of them can undo it; a
failure is reported in the summary instead.

## A restore could report a redis snapshot as loaded when it was not

`scripts/restore.sh` reloads the redis snapshot from your backup. Version 2.13.1
found three defects in that sequence and fixed them — in the weekly *drill*, and
never in the restore script itself. The control got the fix; the thing an
operator runs in an emergency did not.

All three were still there. The script waited a fixed three seconds and then
asked how many keys had loaded, which on a production-sized snapshot answers
"still loading" — reported as an empty backup. It then discarded the reply when
switching the append-only log back on, and `redis-cli` signals success at the
process level even when the server refused. Finally it waited two more seconds
and checked a status field that reads "ok" before any rewrite has run, so it
could not detect the condition it named, while the wait itself was short enough
to cut a real rewrite in half and leave a partial log.

It now waits for the actual conditions and reads the actual replies. It also has
a cleanup handler: the loader container holds your redis data directory, and any
failure between starting it and shutting it down used to leave it running,
mid-restore, with the real service never brought back up.

The drill still *fails* where the restore script *warns*. That difference is
deliberate — a human is watching a restore, whereas the drill exists to go red
on its own.

## The telemetry beacons buffered whatever you sent them

The two anonymous endpoints that accept browser error reports existed to take a
few hundred bytes of JSON, and inherited the 1 GB request limit that exists for
direct file uploads. Both read the whole body before they could reject it — one
explicitly, the other because request validation runs before any handler code,
so no check inside the application could get there first. They are now capped at
64 KB at the edge, which no real report approaches, and the size is checked
before the body is read. The per-address rate limits were always in front of
them, so this was a cost, not an opening.

## Under the hood

**A test suite that was leaking a gigabyte a day of disk.** Three test files
need a real database and skip without one. In CI that database is disposable.
Locally there was no supported way to get one at all — the files said only
"point the connection settings at a throwaway" — so the throwaway was invented
from scratch each time, and the invented one stranded a 167 MB volume per run.
`make test-mariadb` is the supported path now; it cleans up after itself, and a
test fails the build if any script or workflow in this repository starts a
detached database container it does not remove properly. The release pipeline's
own boot test was doing the same thing, harmlessly on disposable build machines
and not harmlessly anywhere else.

**A test file that had never run.** It was written, reviewed and committed,
gated behind the same flag as the migration round-trip, and then named nowhere
in the pipeline that supplies that flag — so it did not fail, it skipped, on
every commit since it was added. It runs now, alongside a new one covering the
database row locks, and the step that runs them says what to do when a third is
added.

## Upgrading

Nothing to do beyond the usual update. Every setting is preserved, no
configuration changes shape, and no service needs restarting by hand.

Two things are worth doing afterwards. If you have ever run the end-to-end
command from your live checkout, check for the seeded accounts described at the
top. And if you keep configuration backups, note that the import fixes above
apply to importing *any* backup, including ones you already have — nothing about
your existing files needs to change.

---

# file:Heron v2.14.0

**Every email the product sends now has an HTML half — and a plain-text one.**

No migration, no host step, no API change, no default moves, no desktop-client
release beside it. What moves is what lands in your recipients' inboxes.

Twelve of the twenty-six emails file:Heron sends had **no HTML template at
all**. They went out as bare `text/plain` and rendered as raw monospace prose:
the operations alert, the server-error alert, the inbound-message notice — and,
more visibly, **the first emails any new user ever receives**: verify your
address, reset your password, you have been invited, and all four
email-change messages. They now use the same restrained editorial layout the
new-device sign-in alert has always used.

**The plain-text part has not gone away, and never could.** Every message is
`multipart/alternative` — a hand-written text part first, the HTML as an
alternative — so a client that refuses HTML sees exactly what it saw before.
That was already true for the fourteen emails that had HTML; it is now true for
all twenty-six.

---

## The release-available email was dead code

`release_available.html.j2` named a layout block that does not exist
(`{% block body %}` where the layout renders `content`), and declared no
`subject` block while the layout asked for one. Rendering it raised
`UndefinedError` on every send. `render_email` caught that exception, set the
HTML body to `None`, **logged nothing at all**, and sent the mail text-only.

So the template was written, translated into German, shipped, and never once
rendered — in either locale, for its entire life. Nothing failed. Nothing was
logged. The email simply arrived plainer than intended, forever.

Nothing in the test suite enumerated the template directory. Of the fifteen
slugs that shipped an HTML template, exactly two had any assertion on their
HTML output at all.

`backend/tests/test_email_template_matrix.py` is the control that was missing:
it takes the slug list from `subjects.json`, requires all four files per slug
(`{en,de}` x `{txt,html}`), compiles each one in its own locale, renders every
combination, and fails if any produces no HTML. It is driven by the shipped
data, never a hand-written list — the two previous times this repo kept "a list
you must remember to update", the list went stale.

## Three faults it turned up on the way

**A syntax error in a German template silently sent the English one.** The
locale fallback caught every exception, not just a missing file — so a broken
`de/` template fell through to `en/` and the recipient got a German text part
beside an English HTML part, with nothing logged. The fallback is now
`TemplateNotFound` only.

**Every German email carried a dangling `Empfangsdatum: .`** — a label with no
date. The footer printed it unconditionally while the value it names is only
ever set for the SMTP connectivity test.

**A rebranded instance still said `file:Heron` in the email header.** The
wordmark was hardcoded, so an operator who set their own application name got
their name in the subject line and the product's name in the header of every
message. Stock installs are unchanged.

## Smaller corrections in the same pass

- The lockout email printed a raw ISO timestamp followed by a hardcoded
  `(UTC)`, which was wrong on any instance with a site timezone set. It now
  renders in the site timezone and names it.
- The operations alert printed its timestamp as a raw ISO string.
- The admin template preview rendered `[UPLOADER]` and `[THREAT]` as blanks for
  the quarantine email, and could only ever show one side of each branch in the
  email-change templates, because the sample context omitted those keys.
- The German session-eviction email greeted the reader in English.
- The inbound-message email pointed at a bare `/admin/inbox` path rather than a
  link you can click.

## Under the hood

The layout vocabulary — eyebrow, serif headline, mono fact table, quote card,
ink call-to-action — is now a set of Jinja macros in
`backend/app/templates/email/_components.html.j2` instead of being copy-pasted
per file. The call-to-action style string alone had twenty-two copies. The
fourteen emails that already looked right are unchanged in substance; they just
compose from the shared pieces now.

---

# file:Heron v2.13.6

**A warning that could never appear, and a check that was skipping a third of
the backend.**

A patch release, and almost all of it is about the checks rather than the
product. No migration, no host step, no change to any default, and no
desktop-client release beside it. One thing moves on the wire and it is
additive: 43 endpoints that had never declared their response shape now do, so
a number of responses carry fields they previously left out — as `null`, or as
the field's own default where it has one. Two are booleans and arrive as
`false` rather than `null`: `already_verified` on
`POST /api/auth/resend-verification`, and `ignored` on the internal tus hook.
Nothing that was sent before is sent differently, and nothing has been
removed.

---

## Importing a backup never warned you that it came from a different version

Before a configuration import, file:Heron shows a dry-run preview: what will be
replaced, how many shares will be invalidated, what cannot be restored. If the
backup was taken on an instance running a different database schema, that
preview is supposed to carry a warning above the summary, so you can stop and
think before replacing your configuration with one that predates a migration.

It has never appeared. Not on your instance, not on any instance, not once
since the feature shipped.

The check compares the schema revision recorded in the backup against the one
this instance is on, and needs both to say anything. Recording it called a
method that does not exist on the migration library's context object. That
raised an error, a catch-all swallowed it, and the function returned "unknown"
every single time — so every backup file ever written recorded its schema
revision as `null`, and a comparison that needs two values never had one.

Both halves are fixed: new backups record the revision, and the preview
compares it.

**Backups you already have still record `null`,** and nothing can retrofit
that — the value was never captured. A backup taken from this release forward
can produce the warning; one taken before it cannot, and will import silently
as it always has. If you keep long-lived backups for disaster recovery, this is
a reason to take a fresh one.

## The type checker was examining two-thirds of the backend and reporting success

file:Heron runs a static type check in CI. It passed on every commit. It was
also skipping 37% of the backend — 18,719 lines — because 47 modules were
exempted wholesale rather than by individual known problem, and the exemption
switches the module off entirely rather than silencing its listed errors.

The exempted set was not a random third. It was every authentication module,
every session module, and the quota, rate-limiting, two-factor, passkey and
storage code — that is, the files where a mistake costs the most. New code
written into any of them was never checked at all.

The list is empty now. All the errors behind it are fixed, the checker is
pinned to an exact version like the linter beside it, and a test fails the
build if an exemption is ever added back. The bug above is what that exemption
list had been hiding.

## The browser app and the API had drifted apart in eight places

The web interface keeps its own hand-written description of every API response.
Nothing compared the two, and they had diverged eight times — most of them
harmless, all of them invisible to the compiler, because each was a wrong field
inside a correctly-named shape rather than a missing one.

The longest-standing: the notification category the instance uses to tell
administrators it is throwing server errors had been missing from that list for
289 commits. Nothing broke — the page renders what the API sends — but every
piece of code that reasoned about "which categories exist" was reasoning from a
list with a hole in it.

A test now reads both sides and fails if they disagree. It found two of the
eight itself. Alongside it, 43 API responses that had no declared shape at all
now have one, which is what makes the comparison possible.

## Corrections

- The reference host was documented as running the previous release and
  awaiting an update. It was already up to date.

## Upgrading

Nothing to do beyond the usual update. Every setting is preserved, no
configuration changes shape, and no service needs restarting by hand.

The one thing worth doing afterwards is taking a fresh configuration backup, if
you keep them: only backups written from this release forward record the schema
revision, and only those can produce the mismatch warning described above.

---

# file:Heron v2.13.5

**An update check that blamed you, and alerts one tap from silence.**

A patch release. It began as "why does *Check for updates* say there is no
backend release" — for most instances the answer was that GitHub was having a
bad afternoon — and ended in the messages, the records and the alerts this
product uses to say that something is wrong. No migration, no host step, no
desktop-client change. Two things do move, both described below: the address the
Updates page offers for the update check, and one endpoint that now refuses two
categories it used to accept.

---

## "Check for updates" blamed your repository for someone else's outage

On 17 August, GitHub's releases list began answering requests with an empty
list: a perfectly successful response that simply contained nothing, while its
own paging headers said there were eight pages of releases to be had. file:Heron
reported this as `no backend release (vX.Y.Z) in GitHub response` — a sentence
about *your* repository and *your* settings. Neither was involved. The newest
release was sitting there, published, and the instance asking the question was
already running it.

Two quite different situations produced that one sentence, and they are fixed by
different people doing different things:

- **nothing came back at all** — the far end is having a problem, or the address
  being asked is wrong; and
- **releases came back, none of them a server release** — the filter, the fork
  or how far back the search reaches is wrong.

They now say so separately, and the second names how many releases it saw and
which was newest, which is exactly what identifies the configuration mistake
described in the next section.

When the request does not complete at all, the reason is legible now too. A
timeout says how long it waited instead of ending in a colon with nothing after
it, which is what an administrator actually saw. An HTTP error leads with its
status code, and a 403 says whether the cause is that this machine's
unauthenticated request allowance with GitHub is spent — worth knowing, because
that allowance is per network address and shared with everything else running on
the same host.

## Opening the update settings and pressing Save broke update checking for good

The Updates settings page pre-fills its address field for you. The address it
offered was left behind by a change in v1.1.8, which moved the update check to a
different GitHub endpoint and did not revisit the settings page. So the field
suggested an address the check cannot use: it returns the newest release of
*any* kind, which for this project is nearly always a desktop-client release and
almost never a server one.

Nothing was wrong until someone opened that page and pressed Save. Saving stored
the suggestion, and from then on every update check — scheduled and manual
alike — failed with precisely the message above, permanently, on an instance
where nothing was actually wrong. The suggestion was written down in three
separate places; they now have one definition, and the build fails if they ever
disagree again.

**If your instance has this saved already, update checking has been failing ever
since.** Open *Settings → Updates*: if the address ends in `/releases/latest`,
replace it with

    https://api.github.com/repos/phoen-ix/fileHeron/releases?per_page=30

The field cannot be cleared to restore the default, so it has to be typed. The
new message names the tag it is seeing, so the cause is now visible rather than
implied. Pointing this at a fork's own `/releases/latest` is still supported —
it is simply no longer what the page hands you unasked.

## A scheduled task that had been failing showed as successful

A scheduled task is recorded as failed when it stops with an error. The update
check does not stop with an error: it catches the problem, records it and
returns normally. So it was written down as a success on every single run, no
matter how long it had been failing — green on the Scheduled tasks page, nothing
in the audit log, nobody told.

Two consecutive scheduled failures now mark the task as failed and raise it the
same way any other failing task is raised. One failure stays quiet deliberately:
the thing being contacted belongs to somebody else, and one bad minute is not
news. Pressing *Check now* never counts toward it either — an administrator
watching an outage presses that button repeatedly, and those presses are not
evidence that anything is broken.

The count is kept rather than the elapsed time, so it means "two scheduled
attempts in a row", whatever cadence you have set the check to.

## One tap in a mail client could switch off the alerts

Operational alerts — a scheduled task failing, a backup failing, a disk filling
up, a burst of server errors — were treated by the mail system as ordinary
notifications somebody might not want. Every one of them therefore carried the
headers that make Gmail and Outlook place an **Unsubscribe** button next to the
sender, and the footer offered the same thing in a single click.

One tap, on one alert that arrived at an inconvenient moment, and this instance
stops telling anyone it is in trouble. Permanently, with nothing recorded
anywhere, and on a small deployment where a single administrator may be the only
person receiving them at all. Losing a share-expiry reminder costs a reminder;
losing these costs the thing that would have told you the alerting had stopped.

Both categories can still be switched off — deliberately, on your notification
preferences page, where it is a decision rather than a reflex. What is gone is
the one-tap route: no Unsubscribe button in the mail client, no unsubscribe link
in the footer, and the equivalent links in mail already delivered no longer work
either, because the refusal is enforced where the change is made rather than
where the link is drawn. The one consequence for anyone automating against the
API: the endpoint behind those links now refuses these two categories, where it
previously performed the change.

They were deliberately *not* made permanently on. That would also have made them
read-only and forced everyone back to the standard channel — which on the
reference instance would have switched off email for the one administrator who
had gone in and deliberately switched it on.

**This release does not turn anyone's notifications back on.** If someone has
already opted out of these, they are still opted out; it is worth a glance at
the preferences of whoever is supposed to be receiving them.

## Fixes found reviewing the above

- The header that offers one-click unsubscribe carried the wrong value — not the
  one the specification fixes, which mail clients match exactly. So one-click
  had most likely never functioned in any client, which is the only reason the
  problem above had not already happened to somebody. Correcting it on its own
  would have *armed* that problem rather than fixed it, so both changed
  together.
- Six comments in the source described behaviour that had not existed for
  several releases. The largest was a table in the background worker listing
  sixteen scheduled jobs and the minute each one ran at, none of which has
  governed anything since v1.28.0, when schedules became editable in the admin
  interface.

---

# file:Heron v2.13.4

**A noisy error log, and the sign-outs that were hiding behind it.**

A patch release. It began as a question about the error log filling with
`TOKEN_EXPIRED` and ended in the session-refresh path, which turned out to sign
people out in three situations where it should not have: when two clients
refreshed at the same moment, when the server was merely restarting, and when
someone mistyped a code during two-factor setup. No migration, no host step, no
API change, no default moves. Desktop client changes ship alongside on their own
tag.

---

## The error log was 78% one harmless event

Turning on 4xx capture with `401` in the code list made the log fill with
`TOKEN_EXPIRED`. Nothing was broken: access tokens last 15 minutes, the web
interface only discovers that a token has expired by making a request that
fails, and the notification bell reconnects every minute — so the bell is always
the thing that finds out first. One entry per fifteen minutes per open tab,
each one followed within the same second by a successful refresh and a
successful retry. Invisible to the user, and forever.

On the reference instance that was 32 of one day's 41 entries, on a four-user
install, and it scales with tabs and hours. The entries the log exists to
surface were being pushed out by an event that is not an error.

`TOKEN_EXPIRED` is no longer recorded. This is deliberately narrow: it is
suppressed by error code, not by status, so every other 401 — a failed sign-in,
a route that refuses a valid session, a scanner probing for credentials — is
still captured exactly as before. That distinction matters: the same 401 capture
caught a real defect on this instance in ninety minutes.

The trade, stated plainly: a genuine mass expiry, such as a host clock jumping,
will no longer show up here. It remains visible in the proxy access log and in
users being asked to sign in again.

## Two tabs refreshing at once could sign you out everywhere

The web interface holds one refresh cookie shared by every tab. Each tab keeps
its own short-lived access token in memory, and refreshes only when one expires.
Open a laptop after it has slept and every tab wakes at once, every token is
already expired, and every tab tries to refresh with the same cookie.

The server allows exactly one of them. What happened to the others depended on
timing, and one of the two outcomes was bad:

- the loser is told its token was already rotated, and that tab returns to the
  sign-in page although the session is alive; or
- the loser's request arrives just after the winner's succeeded, which looks
  identical to somebody replaying a stolen token — so **every session on every
  device is revoked**, and a security event is recorded saying the token was
  reused.

The second one signs you out of your phone and the desktop client because you
opened a laptop lid.

This is not fixable by making the server more forgiving. A replay arriving one
millisecond after a legitimate rotation genuinely cannot be told apart from a
stolen token, and any allowance wide enough to help would also help an attacker.
So the fix is that clients no longer refresh concurrently: the web interface
serialises its refreshes across tabs, and the desktop client across its
threads. Reuse detection is unchanged and still as strict as it was.

The desktop client was the more reliable trigger of the two. A large download
runs several connections from one access token, so when it expired mid-transfer
every connection tried to refresh at once — on every long download, every
fifteen minutes. They now share a single refresh.

## Being returned to the sign-in page when the session really is gone

If a request failed, the session was refreshed successfully, and the retry
failed again, the web interface did nothing at all — the page simply stopped
working, with every subsequent request failing silently. That happens when a
session is revoked or an account is disabled in the moment between the two. It
now returns you to the sign-in page, which is what it always claimed to do.

Fixing that exposed a second problem, fixed in the same release: entering a
wrong code while setting up two-factor authentication is also reported as a
failed request. Left alone, a typo during 2FA setup would have signed the user
out. Every endpoint that rejects a wrong password or code — rather than an
expired session — is now excluded from that path, and the rule is written down
so the list is not guessed at next time.

## Being signed out because the server was restarting

The widest of the three, and the one most likely to have been noticed as "it
logged me out for no reason". Your browser holds a short-lived key that it
renews every fifteen minutes. If renewing it failed, you were signed out — and
*every* kind of failure counted, including "the server did not answer".

Updating file:Heron restarts the server for roughly ten to twenty-five seconds.
Any tab whose key came up for renewal in that window was signed out, with a
perfectly valid session, by the update itself.

Now only an actual answer counts. If the server says the session is over, you
are signed out, exactly as before. If the server cannot be reached — it is
restarting, the network dropped, the request timed out — the session is left
alone; whatever you clicked reports an error, and the next thing you do works
normally once the server is back. The same distinction applies when you load the
page fresh during a restart: the tab no longer stays stuck as signed-out until
you reload it by hand.

There is deliberately no retrying-in-the-background here. A restart lasts longer
than any delay that would not freeze the interface, and the session recovers on
its own within about a minute regardless of whether you do anything.

## A mistyped two-factor code no longer signs you out

If you sign in with single sign-on or a passkey and then mistype your
authenticator code, that was treated the same as an expired session: the app
quietly resent the same wrong code, then signed you out and sent you back
through the whole sign-on round trip. It also counted the mistake twice against
the lockout threshold, so you got half as many attempts as the setting says.

The same shape had already been fixed once during two-factor *setup*. This is
the second place it hid, so the rule is no longer a list someone maintains by
hand: every endpoint that rejects a wrong password or code is now enumerated
from the server automatically, and the build fails if one of them is not
excluded from the retry path.

## Fixes found reviewing the above

Several of these only bite on a self-hosted install reached over plain HTTP,
which is the default for a fresh setup.

- A restart that answered with an unreadable page — a misconfigured proxy, or a
  captive portal on a café network — was read as a *successful* renewal. The app
  then sent every subsequent request with no credentials at all and signed you
  out. It is now treated as "server unreachable", like any other failed renewal.
- If your computer's clock stepped backwards — an automatic time correction, or
  resuming a laptop or virtual machine — the coordination between browser tabs
  could stall every tab for the length of the correction. A timestamp in the
  future is now ignored rather than trusted.
- With several tabs open and the server hung rather than down, tabs could queue
  behind one another indefinitely, freezing navigation and leaving a newly
  opened tab blank. That wait is now bounded.
- A renewal that succeeded could be discarded if the tab-coordination step
  failed immediately afterwards, failing a request whose retry would have
  worked.
- The desktop client could rotate your session twice where once was correct, and
  a resumed download could report "couldn't reach the server" when the real
  cause was an expired session — the exact misreport that was fixed for the
  ordinary case earlier.

---

# file:Heron v2.13.3

**Two more consequences of the same v2.13.1 change, both found by review.**

A patch release, and the third and last instalment of one mistake: v2.13.1 let
an approver reach a live share carrying files awaiting their decision, and did
not revisit everything else that depended on them *not* being able to. v2.13.2
fixed the download budget. These are the remaining two. No migration, no host
step, no API change, no desktop-client change.

Both only bite specific configurations, described below.

---

## The approvals queue listed other people's recipients

An approver's queue returned, for every share in it, the display name and role
of every recipient and the name of every group it was addressed to. For shares
genuinely awaiting approval that is correct and unchanged — deciding whether
something may go out means knowing who it goes to. But v2.13.1 added *live*
shares to that queue when files were appended to them, and for those the
approver is deciding on an attachment, not on the audience. They were shown the
audience anyway.

The rule that governs this everywhere else in the product now has a single
definition, and every place that builds a recipient list is checked against it
automatically — including places not written yet, which is how this one slipped
through: the rule had been applied to the two screens someone thought of, so the
third was built from scratch without it.

Affects instances using four-eyes approval where approvers are not admins. The
web interface never displayed this field, so the exposure was to API clients.

## An approver could be sent to a page that refused them

With content review turned **off**, appending a file to an approved share
emailed the approver a link to that share — and the link returned "you don't
have access". They could still approve it through the API, sight unseen, which
is precisely the blind approval the four-eyes workflow exists to prevent.

The setting says it controls whether approvers may preview or download files
awaiting review, and now that is all it controls. An approver who may decide can
open the share and see what they are deciding on; the file contents, including
filenames, stay hidden unless content review is on, and the download is still
refused.

Affects instances that turned content review off while leaving approval on.

## A queue with no way in

Requiring approval is recorded on each share when it is created, and that record
sticks. So a file appended to such a share is held for review even if four-eyes
has since been switched off — but with it off nobody is notified and the
Approvals link disappears from the menu, while the queue behind it is not empty.
The files stayed held with nothing in the interface leading to the decision that
would release them. The Approvals link now appears whenever there is genuinely
something waiting.

---

# file:Heron v2.13.2

**A regression v2.13.1 introduced, found by reviewing v2.13.1.**

A patch release. One high-severity fix, plus corrections to three checks that
could not detect what their own failure messages claimed. No migration, no host
step, no API change, no default moves. Desktop client **1.4.3** ships alongside
it on its own tag.

---

## An approver reviewing a file could exhaust a share's download budget

v2.13.1 fixed an approver being locked out of the very files they had to decide
on. That fix let a non-admin approver open an active share carrying files
awaiting review — but the download routes still treated "this is a review, not a
delivery" as meaning only *the whole share is awaiting approval*. An approver
reviewing files appended to a **live** share was therefore charged like a
recipient.

On a share limited to one download, the approver's own review spent it, and
every real recipient then got "this share has reached its download limit". The
files were never delivered to anyone.

Both download routes now decide this through one shared rule: access granted
purely by review rights is free, and an approver who is also a recipient still
pays like any other recipient.

This only affects instances using four-eyes approval with content review and a
per-share download limit. If that is you, any share whose budget was consumed
this way can be given more downloads from the share's own page.

## Three checks that could not fail for the reason they named

The restore drill gained real Redis assertions in v2.13.1. Two of them were
wrong in ways that only show up on a bigger instance than the one they were
written against:

- The readiness wait watched for the port to open rather than for the data to
  load, so on a production-sized snapshot the drill could declare a perfectly
  good backup empty. It now waits for Redis to answer with a real key count.
- The check that the rewritten log had succeeded read a field that says "ok"
  before any rewrite has happened, so it could not detect the failure it
  described — and the drill could shut the server down mid-rewrite, causing the
  very problem the next check would then report. It now waits for the rewrite to
  actually finish.
- A refused configuration change was reported as success, because the error text
  was being discarded.

## Corrections

Two sentences in the v2.13.1 notes were wrong and are fixed above them: the
approval-fingerprint fix was about non-ASCII values, not oversized ones, and
four stale comments were corrected in that release, not three.

---

# file:Heron v2.13.1

**Closing the audit backlog — 23 recorded defects, no new features.**

A maintenance release. Nothing here changes how the product is used; it fixes
things that were wrong underneath. No migration, no host step, no API change,
and no default moves. Desktop client **1.4.2** ships alongside it on its own
tag.

Two of these matter more than the rest, and both are controls that were not
controlling anything: the weekly restore drill never actually restored Redis,
and the release pipeline checked its own changelog only after publishing five
images.

---

## The restore drill's Redis step did nothing, and checked nothing

The weekly drill exists to prove the backups restore. It copied the Redis
snapshot into the container and restarted the service — but Redis runs with
append-only mode on, and Redis 7 with AOF enabled ignores `dump.rdb` entirely,
creating an empty log instead of loading the snapshot. The drill then asserted
nothing about the result beyond the file's magic header.

So a quarter of what the drill claimed to prove would have passed identically
against a snapshot of zeros. It now performs the load sequence the production
restore path has used since July, and fails outright if the restored Redis
comes back empty — checked once after loading and again after the restart.

Verified both ways on the reference instance: green against a real backup,
red against a valid but empty snapshot.

## Release notes were checked after the images were already public

The pipeline verified that `RELEASE_NOTES.md` had been rewritten for the tag as
the first step of the *last* job. A tag with stale notes therefore spent the
full test suite, pushed all five images, moved `:latest` on each of them, and
only then failed — leaving the new version live for fresh installs with no
GitHub release attached, and no in-app update banner for existing ones. Release
tags cannot be reused, so recovery meant burning another version number.

The check now runs before anything is built.

## Security and correctness

- **Legal pages that were switched off were still served.** Disabling the
  imprint or privacy page hid it in the app but left the content readable
  directly from the API, so unpublished drafts were reachable.
- **An SSO login could be accepted with an unverified email.** A provider
  reporting `email_verified` as the *string* `"false"` was read as true.
- **Approvers could not see what they were approving.** With four-eyes review
  on, a non-admin approver was notified that files needed review, then refused
  access to the share holding them — they could approve through the API but
  never look first. Scoped to shares that actually have files awaiting review.
- **Cancelling a pending email change could report success without doing
  anything**, if the change had already been applied or cancelled.
- **Releasing a file from quarantine could strand it**, leaving the bytes in
  neither place if the database write failed afterwards; every retry then
  failed permanently.
- **Restoring a configuration backup silently cleared maintenance mode** and
  the low-disk guard.
- **Large inbound emails could be lost.** A message whose text grew past the
  database's packet limit during processing failed after the mailbox had
  already been advanced past it.
- **Re-entering your password could be rejected on the wrong grounds** — the
  browser silently retried a wrong password instead of showing the error.
- **Signing in with a recovery code stalled the server for about two seconds**,
  on an endpoint that needs no login — the ten stored codes were verified one
  after another on the thread serving every other request.
- **Admins were not always told an update had started.** The notification was
  prepared and then discarded.
- Admin-minted API tokens now default to limited scope and a 90-day expiry, as
  the self-service form already did.
- Notification streams no longer leak a reconnect timer, a **non-ASCII**
  approval fingerprint is rejected cleanly instead of erroring the request out,
  and the client-side 404 beacon has an overall ceiling.
- German and English both gained a missing permission label.

## Tests and documentation

Four pieces of test coverage were found not to test what they named. Two
asserted that a phrase appeared in a function's source — and it also appears in
a comment there, so deleting the guard left them green. One walked a hand-written
list of three old database migrations, so it could not see a new one, which is
where the mistake actually gets made; it now scans them all. And the address
check in front of the mail-server "test connection" buttons — the strongest
outbound-request primitive in the product — had no test at all: replacing it
with an empty function broke nothing.

All four were rewritten to fail when the thing they name is removed, and every
fix in this release was checked the same way: revert the fix, confirm the test
goes red, restore.

Four comments describing mechanisms that do not exist were corrected — the
most consequential being the upload-reaper setting, still documented as a cap
on how long an upload may take. It has measured inactivity since v2.12.0, and
the old reading is what killed three live transfers.

---

# file:Heron v2.13.0

**The scan guard's brute-force half could not safely be switched on, and this
release is why.** `signal_auth_failure` classified on the HTTP status alone, and
`TOTP_REQUIRED` — the ordinary two-factor prompt — is a 401. Every normal login
by every 2FA user was therefore counted as credential guessing, at a threshold
of 3 tuned for scanner bait. It also ships a dedicated **Blocked sources** page.

No migration. No host step. No setting changes on upgrade: `signal_auth_failure`
still ships **off**, so this release is behaviour-neutral until you opt in.

---

## Turning on the brute-force signal would have blocked your own office

Measured on a live instance while validating this release. Four sign-ins from
one address — two successful two-factor logins and two mistyped passwords:

| | old behaviour | v2.13.0 |
|---|---|---|
| successful 2FA login | **counted** (`TOTP_REQUIRED` is a 401) | not counted |
| mistyped password | counted | counted |
| total offences | **4** | **2** |

At the shipped threshold of 3, the fourth offence blocks — and the fourth was
the two-factor prompt of a *successful* login. The address would have been
404'd off the entire product at the moment it signed in correctly.

Four things were wrong at once, and all four are fixed:

- **Only the codes that mean a submitted secret was wrong now count.** The
  middleware sees a status, not the error envelope, so the code is published to
  the request scope and matched against an allowlist. `TOTP_REQUIRED` is
  excluded, as are `ACCOUNT_DISABLED` and `EMAIL_NOT_VERIFIED` — both raised
  *after* the password verified, i.e. a confused user, not a guesser. An
  unrecognised code does not count.
- **Four of the six watched paths matched nothing.** `/api/webauthn/` and
  `/api/oidc/` are not routes this app serves (they are under `/api/auth/`), and
  forgot-password, reset-password and register-from-invite answer 200/404/410,
  never 401. Real coverage was `/api/auth/login` alone. The list is now the four
  live credential routes, including `/api/auth/2fa/complete`, and a test pins
  every entry against the router table.
- **Credential failures have their own threshold** (`auth_threshold`, default
  15) and their own counter. Sharing one budget with scanner bait meant two
  probes plus one password typo was a block.
- **A source whose own successful logins explain its failures is exempt** —
  across two different accounts, so a single attacker-held login cannot launder
  a campaign from the same address.

## Blocked sources — a page for what the guard is doing

**Admin → System → Blocked sources.** Previously a small table at the bottom of
the scan-guard settings page.

- Every block with filters for released and expired history, reason, origin and
  address — including "what is blocking *this* address", which finds the range
  containing it, not just an exact match.
- Block an address or range by hand; release; **Release + allow** in one action,
  because releasing without exempting just hands the source back to be
  re-blocked.
- The **allowlist** moved here from the settings form, where it was a free-text
  box. It is now edited one entry at a time under a row lock: as a whole-list
  field on that form, saving the settings page silently erased entries added
  anywhere else.
- A **watchlist** of sources accruing offences that have not been blocked, so a
  scan is visible before it becomes a block. It holds those addresses for at
  most one counting window; turn it off with `scan_guard.watchlist` if you would
  rather it never held them.

## One deliberate refusal

`PUT /api/admin/settings/advanced` **no longer accepts the nine `scan_guard.*`
keys** and answers `400 SETTING_MANAGED_ELSEWHERE`; they are also gone from the
Advanced page. That route wrote them while bypassing the scan-guard page's
side effects, so changing the IPv6 prefix there left live network blocks no
longer matching what the guard computes — their evidence stopped counting and an
orphaned block kept refusing traffic after the visible one was released. Use
**Admin → Settings → Scan guard**, which is now the only writer.

Scripted callers setting those keys through the generic endpoint must move.

## Also fixed

- **IPv4-mapped IPv6 is unwrapped at the door.** `::ffff:8.8.8.8` grouped as
  `::/64` — one prefix covering the entire mapped IPv4 space — so on a
  dual-stack deployment three such sources could have escalated a single block
  over every IPv4 client, and an IPv4 allowlist entry could not have rescued
  them.
- **Releasing a block clears the counters that produced it.** They survived the
  release, so the next offending request re-blocked the source within seconds
  and the release looked like it had done nothing.
- **A manual block no longer folds into an automatic one.** It kept the
  automatic origin, discarded the admin's note and identity, wrote no audit row,
  and could not shorten the block.
- **Admin blocks and releases record the address they came from.** Allowlist
  changes did; blocks and releases did not.
- **`scripts/unblock_ip.py` matches by containment.** Naming your own address
  now clears the range that caught you — it compared strings, so it failed at
  the exact moment it exists for. Host-side releases are also audited now.
- Escalation reads a released block as ended rather than waiting out its
  original expiry, and the scan-guard settings are read in one query rather
  than twenty every fifteen seconds.

## Corrections to earlier claims

Two statements in the docs were false and are now fixed:

- **"Redis down ⇒ the guard fails open"** — it does not. The rate limiter
  catches its own Redis errors and falls back to an in-process counter, so
  probe-path and auth-failure detection keep counting *and blocking* per worker.
  Only the API-404 signal genuinely fails open. The test that covered this
  stubbed a call path that cannot occur.
- The `min_distinct_paths` diversity gate and the authenticated-user exemption
  were both effectively untested; the exemption's test passed whether or not the
  code existed.

## Upgrading

Nothing to do beyond the usual update. No migration, no compose change, no host
step, and every existing setting is preserved. Two new keys appear with
defaults: `scan_guard.auth_threshold` (15) and `scan_guard.watchlist` (on).

If you want the brute-force signal, put your own egress address on the allowlist
**first** — the guard refuses ahead of routing, so a blocked admin cannot reach
the page to undo it. `scripts/unblock_ip.py` on the host is the way back.
