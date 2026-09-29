/* The one dialog that asks for the signed-in user's own password before a
 * protected action. Its contract is what every caller relies on: it emits the
 * password and nothing else, it cannot be dismissed while the request runs, it
 * shows the caller's error in place, and a password never survives a reopen. */
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, describe, expect, it } from 'vitest'

import en from '@/i18n/locales/en.json'

import StepUpDialog from '@/components/StepUpDialog.vue'

const mounted: { unmount: () => void }[] = []

function mountIt(props: Record<string, unknown> = {}) {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(StepUpDialog, {
    props: { open: true, ...props },
    global: { plugins: [i18n] },
    attachTo: document.body,
  })
  mounted.push(w)
  return w
}

type W = ReturnType<typeof mountIt>
const confirmBtn = (w: W) => w.find<HTMLButtonElement>('[data-testid="step-up-confirm"]')
const pressEscape = () =>
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))

afterEach(() => {
  while (mounted.length) mounted.pop()!.unmount()
})

describe('StepUpDialog', () => {
  it('renders nothing while closed', () => {
    const w = mountIt({ open: false })
    expect(w.find('[data-testid="step-up-dialog"]').exists()).toBe(false)
  })

  it('shows the default title, the message and a focused password field', async () => {
    const w = mountIt({ message: 'Because this can leak secrets.' })
    await flushPromises()
    expect(w.text()).toContain(en.common.confirm_your_password)
    expect(w.text()).toContain('Because this can leak secrets.')
    const input = w.find('[data-testid="step-up-password"]')
    expect(input.attributes('type')).toBe('password')
    expect(input.attributes('autocomplete')).toBe('current-password')
    expect(document.activeElement).toBe(input.element)
  })

  it('confirms only with a password, and emits exactly that password', async () => {
    const w = mountIt({ confirmLabel: 'Export' })
    expect(confirmBtn(w).text()).toBe('Export')
    expect(confirmBtn(w).element.disabled).toBe(true)
    await w.find('[data-testid="step-up-password"]').setValue('s3cret')
    expect(confirmBtn(w).element.disabled).toBe(false)
    await w.find('form').trigger('submit')
    expect(w.emitted('confirm')).toEqual([['s3cret']])
  })

  it('cancels on Escape, the backdrop and the Cancel button', async () => {
    const w = mountIt()
    pressEscape()
    await w.find('[data-testid="step-up-dialog"]').trigger('click')
    await w.find('button[type="button"]').trigger('click')
    expect(w.emitted('cancel')).toHaveLength(3)
  })

  it('cannot be dismissed or resubmitted while the request runs', async () => {
    const w = mountIt({ busy: true })
    await w.find('[data-testid="step-up-password"]').setValue('s3cret')
    pressEscape()
    await w.find('[data-testid="step-up-dialog"]').trigger('click')
    await w.find('form').trigger('submit')
    expect(w.emitted('cancel')).toBeUndefined()
    expect(w.emitted('confirm')).toBeUndefined()
    expect(confirmBtn(w).element.disabled).toBe(true)
  })

  it("shows the caller's error in the dialog", () => {
    const w = mountIt({ error: 'Password incorrect.' })
    expect(w.find('[role="alert"]').text()).toBe('Password incorrect.')
  })

  it('never carries a password into the next opening', async () => {
    const w = mountIt()
    await w.find('[data-testid="step-up-password"]').setValue('s3cret')
    await w.setProps({ open: false })
    await w.setProps({ open: true })
    await flushPromises()
    const input = w.find<HTMLInputElement>('[data-testid="step-up-password"]')
    expect(input.element.value).toBe('')
  })

  it('marks a destructive confirm', () => {
    const w = mountIt({ danger: true })
    expect(confirmBtn(w).classes()).toContain('fh-btn-danger')
  })
})
