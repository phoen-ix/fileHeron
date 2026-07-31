# file:Heron v2.7.0

**Everything current.** Python 3.14, Node 24 LTS, Alpine 3.24, TypeScript 6,
ESLint 10, Vite 8, Pinia 4 - plus the whole dependency tail. No host step, no
migration, no API change. Every open dependency PR is resolved: eleven were
proposed, and the result is not the eleven merges it looks like.

> If you self-host from source rather than the published images, note that the
> backend now declares `requires-python = ">=3.14"`, because that is the only
> runtime it is built and tested on.

---

## The interesting part: half of them could not be merged

Automated dependency PRs bump one package at a time. Toolchains do not move one
package at a time, and four of these could not have passed no matter how many
times they were rebased:

- **TypeScript 7** removes the `./lib/tsc` export that `vue-tsc` calls, so the
  frontend **image build dies**. Merged, the next release tag would have
  published no frontend image, and the in-app updater would have had nothing to
  pull. This release goes to TypeScript **6.0.3** instead - the ceiling until
  `vue-tsc` and `typescript-eslint` (whose peer range still ends below 6.1) ship
  TS 7 support.
- **ESLint 10** alone fails: the config imports `@eslint/js`, which was never
  declared and resolved only because ESLint 9 hoisted its own copy. It also
  needs `eslint-plugin-vue` 10, and a `globals.browser` declaration - ESLint 10
  stopped assuming an environment, so without it `no-undef` fires 122 times on
  `window` and `document` and it looks like the code broke.
- **Node 25** is an odd-numbered release: Current, not LTS, six months of
  support and then nothing. It was proposed for the image that builds the SPA
  published for public self-hosting. This release uses **Node 24**, the Active
  LTS.
- **`@types/node` 26** was the only fully green PR of the set, and the one most
  worth declining. The runtime is Node 24; types two majors ahead type-check
  APIs that do not exist where the code runs, so the build passes and the image
  fails. Types now track the runtime.

Those four constraints are recorded in `.github/dependabot.yml`, because all
four were re-proposed within minutes of being rejected.

## Runtimes

Python **3.12 -> 3.14** and Node **22 -> 24 LTS** across every image, with
Alpine 3.24 for the updater shim.

The important half of that is what it forced into alignment. CI pinned its own
Python and Node versions independently of the images, so before this release
every unit gate tested a runtime that was not the one shipping. They match now.
The desktop client stays on Python 3.12 deliberately - it bundles its own
interpreter, so its tests must match what is actually shipped in the `.exe`.

Two things surfaced on the way and were fixed rather than carried:

- `ruff`'s target version follows `requires-python`, so moving to 3.14 turned on
  a rule the codebase had two instances of. That is the same mechanism that once
  shipped a red lint gate.
- A newer Starlette deprecates its synchronous test client. The last test still
  using it now drives the app the way the rest of the suite does, and covers all
  three byte-serving routes instead of one.

## A flag that should not have outlived its reason

`frontend/.npmrc` carried `legacy-peer-deps=true`. Its comment explained
exactly why: the app stayed on Pinia 2 while `vue-router` 5 declared an optional
peer on Pinia 3.

Pinia 4 satisfies that peer, so the flag is gone - and that matters more than it
sounds. `legacy-peer-deps` suppresses **every** peer conflict, not the one it was
added for. For as long as it was there, a genuinely incompatible dependency
would have installed silently rather than failing. Peer resolution is strict
again, verified by installing from the lock file with no `.npmrc` present and
building the real image without it.

## Everything else

Vite 8, Vitest 4.1.10, Pinia 4, `@vitejs/plugin-vue` 6, Vue 3.5.40, vue-router
5.2.0, vue-i18n 11.4.8, ruff 0.16.0, nine GitHub Actions, three ProseMirror
patches, the fonts, Prettier and typescript-eslint.

**happy-dom 15 -> 20** is worth calling out on its own: it is a test-only
dependency, and it clears three CRITICAL advisories that `npm audit
--omit=dev` cannot see, because that command only looks at production
dependencies.

One real piece of dead code turned up: a template ref in the upload component
that was declared, bound and never read. The file picker opens through the
wrapping `<label>`, so it did nothing.

## Verification

Every image was built and run, not just type-checked: the backend suite
(1705 tests) executed **inside** the Python 3.14 image, which still runs as UID
1000 - the property the `data/` bind mounts depend on; the frontend image built
on Node 24 with its nginx config tested; the updater executor and shim built and
their runtimes confirmed. The frontend gate - install from lock, `vue-tsc -b` +
`vite build`, lint, 184 tests - was run against **current main content** rather
than each PR's stale base, because main had moved five releases since the oldest
of them opened.

## Upgrading

In-app Update, or `FH_TAG=v2.7.0`. Nothing else to do.
