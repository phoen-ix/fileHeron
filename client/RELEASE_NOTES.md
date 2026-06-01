# Desktop client 0.9.5

Fixes broken large-file uploads, and removes the remaining error pop-ups.

## Uploads work again (TUS 308 fix)

Uploading a file larger than 100 MB failed with *"TUS PATCH failed: HTTP 308
Permanent Redirect."* The resumable-upload URL handed back by the server could
carry the wrong scheme (`http://` instead of `https://`) when the server sits
behind a TLS-terminating proxy, and the client then got bounced by a redirect
it couldn't follow. The client now pins the upload to the exact server it
connected to, so the redirect never happens. Large uploads go through.

*(There's also a matching server-side fix in the reverse-proxy config so the
upload URL is correct at the source — deploy it to fix large uploads in the web
app too.)*

## No more error pop-ups

Every remaining error and validation message — upload failures, "add a file
first", invalid expiry/limit, load/download errors — now appears as a brief
**toast** at the bottom of the window or inline, instead of a dialog box you
have to click away. The only thing that still asks for a click is the
**"End share?"** confirmation, because that one deletes data.

---

**Requires:** Windows. Download `fileheron-client.exe` below.
