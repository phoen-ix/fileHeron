/* A field takes a click only on its label's words and on its control.
 *
 * A `<label>` that wraps its control is one click target as large as its box:
 * the label text, the control, the help text under it and every gap between
 * them, across the whole column. A click on the help text or in the empty
 * space beside the label jumped into the field - and on the recipient field
 * opened its list. Well over a hundred fields are built this way (`.fh-field`
 * and three page-local patterns); this cancels the jump for all of them
 * instead of rebuilding each.
 *
 * `preventDefault()` on the click is what cancels a label's activation. It
 * runs for a click on the label's OWN box (the gaps, the space beside its
 * children) and on help or error text; the label's words, the control and
 * anything interactive are untouched, and the help text can still be selected.
 * A checkbox or radio label is left alone: there the whole row is the target
 * on purpose.
 *
 * The other half is CSS: the element holding a label's words is as wide as
 * the words (`.fh-field-label` in global.css, `width: fit-content`). Stretched
 * across its row, that element IS the empty space beside the label. The e2e
 * route sweeps check both halves with a real mouse on every page
 * (`expectPreciseFieldLabels`). */
const NOT_THE_LABEL = '.fh-field-help, .fh-field-error'

function isToggle(control: Element): boolean {
  return control instanceof HTMLInputElement && ['checkbox', 'radio'].includes(control.type)
}

export function installFieldLabelGuard(root: Document = document): void {
  root.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target : null
    const label = target?.closest('label')
    const control = label?.control
    if (!target || !label || !control || !label.contains(control) || isToggle(control)) return
    const aside = target.closest(NOT_THE_LABEL)
    if (target === label || (aside && label.contains(aside))) event.preventDefault()
  })
}
