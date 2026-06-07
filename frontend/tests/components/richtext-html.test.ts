import { describe, expect, it } from 'vitest'

import { docToHtml, htmlToDoc } from '@/components/richtext/html'

/** Round-trip an HTML string through doc <-> html and return the result. */
function roundTrip(html: string): string {
  return docToHtml(htmlToDoc(html))
}

describe('richtext HTML round-trip', () => {
  it('preserves headings, paragraphs and inline marks', () => {
    const out = roundTrip('<h2>Title</h2><p><strong>bold</strong> <em>it</em> <u>u</u> <s>s</s></p>')
    expect(out).toContain('<h2>Title</h2>')
    expect(out).toContain('<strong>bold</strong>')
    expect(out).toContain('<em>it</em>')
    expect(out).toContain('<u>u</u>')
    expect(out).toContain('<s>s</s>')
  })

  it('preserves alignment as a text-* class (not inline style)', () => {
    const out = roundTrip('<p class="text-center">centered</p>')
    expect(out).toContain('class="text-center"')
    expect(out).not.toContain('style')
  })

  it('drops an unknown alignment class', () => {
    const out = roundTrip('<p class="text-center bogus">x</p>')
    expect(out).toContain('text-center')
    expect(out).not.toContain('bogus')
  })

  it('preserves lists', () => {
    const out = roundTrip('<ul><li><p>one</p></li><li><p>two</p></li></ul>')
    expect(out).toContain('<ul>')
    expect(out).toContain('one')
    expect(out).toContain('two')
  })

  it('preserves a table', () => {
    const out = roundTrip('<table><tbody><tr><td><p>a</p></td><td><p>b</p></td></tr></tbody></table>')
    expect(out).toContain('<table>')
    expect(out).toContain('a')
    expect(out).toContain('b')
  })

  it('preserves links with rel and images', () => {
    const out = roundTrip('<p><a href="https://x">l</a></p><p><img src="https://x/y.png" alt="pic"></p>')
    expect(out).toContain('href="https://x"')
    expect(out).toContain('rel="noopener noreferrer nofollow"')
    expect(out).toContain('src="https://x/y.png"')
    expect(out).toContain('alt="pic"')
  })

  it('drops markup outside the schema (e.g. raw script/div)', () => {
    const out = roundTrip('<div>wrap</div><script>alert(1)</script>')
    expect(out).not.toContain('<script>')
    expect(out).toContain('wrap')
  })
})
