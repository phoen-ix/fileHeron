/* The self-service token panel had no test. Its create form's DEFAULTS are the
 * control: least privilege (90 days, limited scopes) is what makes "just click
 * through" produce a bounded credential. Cancel used to reset the form to the
 * pre-hardening defaults (never expires, full access), so the SECOND time the
 * form was opened it offered a permanent unrestricted token by default while
 * the first time did not. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const listTokens = vi.fn(async () => ({ data: { items: [], can_create: true } }))
vi.mock('@/api/apiTokens', () => ({
  listTokens: () => listTokens(),
  createToken: vi.fn(),
  revokeToken: vi.fn(),
}))

import ApiTokenPanel from '@/components/ApiTokenPanel.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(ApiTokenPanel, { global: { plugins: [i18n] } })
}

type Wrapper = ReturnType<typeof makeWrapper>

function button(w: Wrapper, text: string) {
  const hit = w.findAll('button').filter((b) => b.text().trim() === text)[0]
  if (!hit) throw new Error(`no button labelled ${JSON.stringify(text)}`)
  return hit
}

function radio(w: Wrapper, value: string): HTMLInputElement {
  return w.find(`input[type="radio"][value="${value}"]`).element as HTMLInputElement
}

/** The ExpiryPicker preset the form currently highlights. */
function activePreset(w: Wrapper): string | undefined {
  return w
    .findAll('.preset-btn')
    .filter((b) => b.attributes('aria-pressed') === 'true')[0]
    ?.text()
}

describe('ApiTokenPanel create form', () => {
  beforeEach(() => {
    listTokens.mockClear()
  })

  it('opens with the least-privilege defaults', async () => {
    const w = makeWrapper()
    await flushPromises()
    await button(w, 'Create token').trigger('click')

    expect(radio(w, 'limited').checked).toBe(true)
    expect(radio(w, 'full').checked).toBe(false)
    expect(activePreset(w)).toBe('90 days')
  })

  it('cancelling does not downgrade the defaults for the next open', async () => {
    const w = makeWrapper()
    await flushPromises()
    await button(w, 'Create token').trigger('click')

    // Widen both, then back out.
    await w.find('input[type="radio"][value="full"]').setValue()
    await button(w, 'Never').trigger('click')
    expect(radio(w, 'full').checked).toBe(true)
    expect(activePreset(w)).toBe('Never')
    await button(w, 'Cancel').trigger('click')

    await button(w, 'Create token').trigger('click')
    expect(radio(w, 'limited').checked).toBe(true)
    expect(radio(w, 'full').checked).toBe(false)
    expect(activePreset(w)).toBe('90 days')
  })
})
