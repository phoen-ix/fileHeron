/* Every eyebrow (the small label above a page heading) comes from the locale
 * files. Five auth pages wrote theirs as literal English ("/ verify",
 * "/ inbox", "done", "invite", "/ account / 2fa"), which a German UI showed
 * untranslated. Scanned over every .vue, so a new page is held to it too. */
import { describe, expect, it } from 'vitest'

const SOURCES = import.meta.glob('/src/**/*.vue', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

// An attribute run may hold a quoted `>` (`v-if="n > 0"`).
const ATTRS = String.raw`(?:[^>"]|"[^"]*")*`
const EYEBROW = new RegExp(
  String.raw`<(\w+)\b${ATTRS}\sclass="[^"]*\bfh-eyebrow\b[^"]*"${ATTRS}>([\s\S]*?)</\1\s*>`,
  'g',
)

/** The text of an HTML fragment, as the browser parses it (nested tags included). */
const textOf = (html: string) => new DOMParser().parseFromString(html, 'text/html').body.textContent ?? ''

/** The literal words of each eyebrow, outside its `{{ … }}` interpolations. */
function literalEyebrows(src: string): string[] {
  return [...src.matchAll(EYEBROW)]
    .map((m) => textOf(m[2]!.replace(/\{\{[\s\S]*?\}\}/g, '')))
    .filter((text) => /\p{L}/u.test(text))
    .map((text) => text.trim())
}

describe('eyebrows', () => {
  it('carry no literal words', () => {
    const offenders = Object.entries(SOURCES).flatMap(([path, src]) =>
      literalEyebrows(src).map((text) => `${path}: ${text}`),
    )
    expect(offenders).toEqual([])
    const total = Object.values(SOURCES).reduce((n, src) => n + [...src.matchAll(EYEBROW)].length, 0)
    expect(total).toBeGreaterThanOrEqual(30)
  })

  it('the scan catches what it is for', () => {
    expect(literalEyebrows('<span class="fh-eyebrow fh-rise" data-stagger="1">/ verify</span>')).toEqual([
      '/ verify',
    ])
    expect(literalEyebrows('<p v-if="n > 0" class="fh-eyebrow">done</p>')).toEqual(['done'])
    expect(literalEyebrows('<span class="fh-eyebrow">/ <b>account</b> / 2fa</span>')).toEqual([
      '/ account / 2fa',
    ])
    expect(literalEyebrows(`<span class="fh-eyebrow">/ {{ $t('verify.eyebrow') }}</span>`)).toEqual([])
    expect(literalEyebrows(`<span class="fh-eyebrow"\n  >/ {{ $t('a.b') }}</span\n>`)).toEqual([])
  })
})
