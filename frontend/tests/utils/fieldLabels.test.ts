/* Only a field's label text and its control take a click (utils/fieldLabels).
 *
 * A wrapping <label> makes its whole box one click target: 91 fields wrap
 * label, control and help text that way, so a click on the help text or in the
 * empty space beside the label jumped into the field. The width half of the
 * rule is CSS (`.fh-field-label` in global.css), which happy-dom does not lay
 * out: backend/tests/test_frontend_field_layout.py and the e2e route sweeps
 * cover that. */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { installFieldLabelGuard } from '@/utils/fieldLabels'

import { click } from '../helpers/user'

beforeAll(() => installFieldLabelGuard(document))
afterEach(() => {
  document.body.innerHTML = ''
})

function field(inner = '') {
  document.body.innerHTML = `
    <label class="fh-field" id="field">
      <span class="fh-field-label" id="text">Download limit</span>
      <input id="control" type="number" />
      <span class="fh-field-help" id="help">Leave blank for <b id="bold">unlimited</b>.</span>
      ${inner}
    </label>`
  const el = (id: string) => document.getElementById(id) as HTMLElement
  return { el, control: el('control') }
}

describe('field label guard', () => {
  it('a click on the help text does not jump into the field', async () => {
    const { el, control } = field()
    await click(el('help'))
    expect(document.activeElement).not.toBe(control)
    await click(el('bold'))
    expect(document.activeElement).not.toBe(control)
  })

  it("a click on the label's own box - the gaps, the space beside the text - does not either", async () => {
    const { el, control } = field()
    await click(el('field'))
    expect(document.activeElement).not.toBe(control)
  })

  it('a click on the label text still focuses the field', async () => {
    const { el, control } = field()
    await click(el('text'))
    expect(document.activeElement).toBe(control)
  })

  it('the control and anything interactive inside the label keep their click', async () => {
    const pressed = vi.fn()
    const { el, control } = field(
      '<ul><li><button type="button" id="suggest">Pick</button></li></ul><a id="link" href="#x">docs</a>',
    )
    el('suggest').addEventListener('click', (e) => pressed(e.defaultPrevented))
    await click(el('suggest'))
    expect(pressed).toHaveBeenCalledWith(false)

    let prevented: boolean | null = null
    el('link').addEventListener('click', (e) => {
      prevented = e.defaultPrevented
      e.preventDefault()
    })
    await click(el('link'))
    expect(prevented).toBe(false)

    await click(control)
    expect(document.activeElement).toBe(control)
  })

  it('holds for a field built with any other class', async () => {
    document.body.innerHTML = `
      <label class="num-field" id="row">
        <span id="words">Retention</span>
        <input id="control" type="number" />
      </label>`
    const control = document.getElementById('control')
    await click(document.getElementById('row') as HTMLElement)
    expect(document.activeElement).not.toBe(control)
    await click(document.getElementById('words') as HTMLElement)
    expect(document.activeElement).toBe(control)
  })

  it('leaves a label that points at its field from outside alone', async () => {
    document.body.innerHTML = `
      <label for="control" id="for">Email</label>
      <input id="control" type="email" />`
    await click(document.getElementById('for') as HTMLElement)
    expect(document.activeElement).toBe(document.getElementById('control'))
  })

  it('leaves a checkbox label alone: there the whole row is the target', async () => {
    document.body.innerHTML = `
      <label class="toggle"><input id="box" type="checkbox" />
        <span><span id="name">Notify</span><span class="fh-field-help" id="help">by email</span></span>
      </label>`
    const help = document.getElementById('help') as HTMLElement
    let prevented = true
    help.addEventListener('click', (e) => {
      queueMicrotask(() => (prevented = e.defaultPrevented))
    })
    await click(help)
    await Promise.resolve()
    expect(prevented).toBe(false)
    expect(document.activeElement).toBe(document.getElementById('box'))
  })
})
