# Desktop client 0.9.8

Fixes the window freezing during downloads, and makes the debug logs reachable
from Settings.

## Window no longer freezes during a download

While a download ran, the app window was unresponsive until it finished. The
download already ran in the background, but it reported progress thousands of
times (per chunk, across several connections) and that flood of UI updates
starved the main loop. Progress updates are now coalesced (only the latest is
applied, a few times per second), and downloads read in larger chunks — so the
progress bar moves smoothly and the window stays responsive the whole time. (Same
fix benefits large uploads.)

## Open the debug logs from Settings

Verbose logging already had a toggle in **Settings → Diagnostics**; now there's
an **"Open log folder"** button right next to it that reveals
`crash.log` / `trace.log` / `app.log` in your file manager, so you can grab them
for analysis. Enabling verbose logging still takes effect after the next restart
(Settings now reminds you).

---

**Requires:** Windows. Download `fileheron-client.exe` below.
