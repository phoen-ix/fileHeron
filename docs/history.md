# history

Narrative moved out of `CLAUDE.md`; the rules themselves stay there.

## How CLAUDE.md got its shape

**CLAUDE.md was 1,990 lines on 2026-09-13, cut to 796, and trimmed again on
2026-09-20 from 857 lines / 21,642 words to 832 lines / 19,733 words.** What came
out both times: release narrative, dated incident evidence, row counts, and
CLAUDE.md's corrections of its own past wording; every rule kept its operative
clause and one reason clause. Pre-trim copies: `git show e516bf1:CLAUDE.md`
(1,990 lines) and `git show 3560396:CLAUDE.md` (857) - a commit SHA is
immutable, `RELEASE_NOTES.md` is not. **Two conventions keep it from
regrowing:** one rule per line, and a rule lives under the subsystem it
governs, never under the release that found it.

**On 2026-10-02 it was split** (199,273 characters before): area detail
moved word for word to `docs/engineering/<area>.md`, and CLAUDE.md keeps the
cross-cutting rules, a routing table and the traps that do the most damage per
area (now §Invariants › Per area). Pre-split copy: `git show 311ede2:CLAUDE.md`.
Area detail goes to its doc, not back into CLAUDE.md.

**On 2026-10-23 it was cut to 300 lines** for the fleet docs audit: the
narrative above moved here, the Architecture paragraph went (README §Architecture
owns it), schema rows the models state outright went, paragraphs were unwrapped
to one line each, and every rule now sits under `## Invariants`. Pre-cut copy:
`git show da751e0:CLAUDE.md`.
