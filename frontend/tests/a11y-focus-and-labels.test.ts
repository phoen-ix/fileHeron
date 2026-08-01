/**
 * Keyboard and screen-reader affordances that were measured to be missing.
 *
 * From audit #2:
 *  - the app-wide focus ring was a 35% wash of the accent, which composites to
 *    1.63:1 on the warm paper background - below the 3:1 WCAG 1.4.11 floor for
 *    a non-text indicator, on every control on every page;
 *  - the share and SSO tables set `outline: none` on focus and relied on a
 *    background from `--fh-hover`, a custom property that was never defined
 *    anywhere, so focus moved through 25 clickable rows with NO indicator at
 *    all and Enter opened whichever row happened to hold it;
 *  - every runtime tunable on /admin/settings/advanced was a control with no
 *    accessible name; the boolean was wrapped in an EMPTY <label>, which is
 *    precisely the shape that satisfies the lint rule while giving assistive
 *    technology nothing.
 *
 * These are file-level assertions on purpose: contrast and "is this property
 * defined" are not observable from a mounted component, and the failure mode
 * here was a value that looked plausible in the source.
 */
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const SRC = resolve(__dirname, '../src')
const read = (p: string) => readFileSync(resolve(SRC, p), 'utf8')

/** Every .vue/.css file under src, so a missing token cannot hide in a
 *  component nobody thought to list. */
function globSync(dir = SRC, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = resolve(dir, entry.name)
    if (entry.isDirectory()) globSync(full, out)
    else if (/\.(vue|css)$/.test(entry.name)) out.push(full)
  }
  return out
}

function srgbToLinear(c: number): number {
  const s = c / 255
  return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4)
}

function luminance(hex: string): number {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) throw new Error(`not a 6-digit hex colour: ${hex}`)
  const n = parseInt(m[1], 16)
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255]
  return 0.2126 * srgbToLinear(r) + 0.7152 * srgbToLinear(g) + 0.0722 * srgbToLinear(b)
}

function contrast(a: string, b: string): number {
  const [la, lb] = [luminance(a), luminance(b)]
  const [hi, lo] = la > lb ? [la, lb] : [lb, la]
  return (hi + 0.05) / (lo + 0.05)
}

function token(name: string): string {
  const css = read('styles/tokens.css')
  const m = new RegExp(`--${name}:\\s*([^;]+);`).exec(css)
  if (!m) throw new Error(`token --${name} is not defined`)
  return m[1].trim()
}

describe('focus indicator', () => {
  it('meets the WCAG 1.4.11 3:1 floor against every page surface', () => {
    const ring = token('fh-focus-ring')
    expect(ring, 'a translucent ring composites to less than it looks').toMatch(
      /^#[0-9a-f]{6}$/i,
    )
    for (const surface of ['fh-paper', 'fh-paper-raised', 'fh-paper-sunk']) {
      expect(contrast(ring, token(surface)), `ring on --${surface}`).toBeGreaterThanOrEqual(3)
    }
  })

  it('the control the sanity of this test depends on', () => {
    // The value that shipped, composited by hand: 0.35 * #b45309 over #faf8f3.
    expect(contrast('#e2bea1', '#faf8f3')).toBeLessThan(3)
  })
})

describe('clickable table rows', () => {
  const rowViews = ['views/ShareList.vue', 'views/AdminSettingsSSOList.vue']

  it('never remove the outline from a focusable row', () => {
    for (const view of rowViews) {
      const focusBlocks = read(view).match(/tbody tr:focus-visible\s*\{[^}]*\}/g) ?? []
      expect(focusBlocks.length, `${view} has no :focus-visible rule`).toBeGreaterThan(0)
      for (const block of focusBlocks) {
        expect(block, `${view}: focus ring removed`).not.toMatch(/outline:\s*none/)
        expect(block, `${view}: no visible ring`).toMatch(/outline:\s*2px solid/)
      }
    }
  })

  it('reference only custom properties that exist', () => {
    // Widened past the two row views on purpose: `--fh-hover` was not the only
    // one. `--fh-rule` was used for a border in twenty components and defined
    // nowhere, so those borders fell back to `currentColor` (audit #2).
    const css = read('styles/tokens.css')
    const files = globSync()
    const missing = new Set<string>()
    for (const file of files) {
      for (const [, name] of readFileSync(file, 'utf8').matchAll(
        /var\((--fh-[a-z0-9-]+)[,)]/g,
      )) {
        if (!css.includes(`${name}:`)) missing.add(`${name} (${file.replace(SRC, '')})`)
      }
    }
    expect([...missing]).toEqual([])
  })
})

describe('advanced settings tunables', () => {
  const view = read('views/AdminSettingsAdvanced.vue')

  it('give every control an accessible name', () => {
    expect(view).toMatch(/<label class="field-label" :for="`tunable-\$\{it\.key\}`"/)
    const controls = view.match(/<input\b[\s\S]*?\/>/g) ?? []
    expect(controls.length).toBeGreaterThanOrEqual(3)
    for (const c of controls) {
      expect(c, 'a tunable control with no id to label').toContain('`tunable-${it.key}`')
    }
  })

  it('no longer wrap a control in an empty label', () => {
    // The exact shape that passed `form-control-has-label` while conveying
    // nothing: <label class="switch"><input type="checkbox" /></label>.
    expect(view).not.toMatch(/<label[^>]*class="switch"/)
  })
})
