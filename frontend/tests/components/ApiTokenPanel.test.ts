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

const listTokens = vi.fn(async () => ({ data: { items: [] as unknown[], can_create: true } }))
const revokeToken = vi.fn(async (_id: number) => ({}))
const createToken = vi.fn()
vi.mock('@/api/apiTokens', () => ({
  listTokens: () => listTokens(),
  createToken: (...args: unknown[]) => createToken(...args),
  revokeToken: (id: number) => revokeToken(id),
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

describe('ApiTokenPanel password dialog', () => {
  // The dialog and the form shared one error: "Password incorrect." stayed
  // under the form after the dialog that asked for the password was dismissed.
  it('takes its error with it when dismissed', async () => {
    createToken.mockRejectedValueOnce(
      Object.assign(new Error('INVALID_PASSWORD'), {
        isAxiosError: true,
        response: { status: 403, data: { code: 'INVALID_PASSWORD', error: 'Password incorrect.' } },
      }),
    )
    const w = makeWrapper()
    await flushPromises()
    await button(w, 'Create token').trigger('click')
    await w.find('.create-form input[type="text"]').setValue('ci')
    await w.find('.create-form').trigger('submit')
    await w.find('[data-testid="step-up-password"]').setValue('nope')
    await w.find('[data-testid="step-up-dialog"] form').trigger('submit')
    await flushPromises()
    expect(w.find('[data-testid="step-up-dialog"] [role="alert"]').exists()).toBe(true)

    await w
      .find('[data-testid="step-up-dialog"]')
      .findAll('button')
      .find((b) => b.text() === en.common.cancel)!
      .trigger('click')
    expect(w.find('[data-testid="step-up-dialog"]').exists()).toBe(false)
    expect(w.find('.create-form .fh-field-error').exists()).toBe(false)
  })
})

describe('ApiTokenPanel failures are shown, not swallowed', () => {
  const envelope = (code: string, status: number) =>
    Object.assign(new Error(code), {
      isAxiosError: true,
      response: { status, data: { code, error: code } },
    })

  it('a failed load is an error, not "No API tokens yet"', async () => {
    listTokens.mockRejectedValueOnce(envelope('SERVER_ERROR', 500))
    const w = makeWrapper()
    await flushPromises()
    expect(w.find('[role="alert"]').exists()).toBe(true)
    expect(w.text()).not.toContain(en.api_tokens.empty)
  })

  it('a refused revoke raises a toast and keeps the token listed', async () => {
    listTokens.mockResolvedValueOnce({
      data: {
        items: [
          {
            id: 5,
            name: 'ci',
            prefix: 'fh_abcd1234',
            scopes: null,
            expires_at: null,
            last_used_at: null,
            created_at: '2026-09-01T00:00:00',
            revoked_at: null,
            disabled_at: null,
            owner_display_name: null,
          },
        ],
        can_create: true,
      },
    } as never)
    revokeToken.mockRejectedValueOnce(envelope('TOKEN_NOT_FOUND', 404))
    const w = makeWrapper()
    await flushPromises()
    const { useUiStore } = await import('@/stores/ui')
    const ui = useUiStore()
    vi.spyOn(ui, 'confirm').mockResolvedValue(true)
    const revoke = w.findAll('button').find((b) => b.text().trim() === en.api_tokens.revoke)
    await revoke!.trigger('click')
    await flushPromises()
    expect(revokeToken).toHaveBeenCalledWith(5)
    expect(ui.toasts.some((t) => t.tone === 'error')).toBe(true)
    expect(w.text()).toContain('ci')
  })
})

describe('ApiTokenPanel asks for the password in a dialog', () => {
  const dialog = (w: Wrapper) => w.find('[data-testid="step-up-dialog"]')

  async function fillAndSubmit(w: Wrapper, { open = true } = {}) {
    if (open) await button(w, 'Create token').trigger('click')
    await w.find('input[type="text"]').setValue('ci')
    await w.find('input[type="radio"][value="full"]').setValue()
    await w.find('form.create-form').trigger('submit')
    await flushPromises()
  }

  beforeEach(() => {
    createToken.mockReset()
  })

  it('the form holds no password; the dialog does, and creating sends it', async () => {
    createToken.mockResolvedValue({ data: { plaintext_token: 'fh_x_y', id: 1 } })
    const w = makeWrapper()
    await flushPromises()
    await button(w, 'Create token').trigger('click')
    expect(w.find('input[type="password"]').exists()).toBe(false)
    await fillAndSubmit(w, { open: false })
    expect(createToken).not.toHaveBeenCalled()
    expect(dialog(w).exists()).toBe(true)
    await w.find('[data-testid="step-up-password"]').setValue('pw')
    await dialog(w).find('form').trigger('submit')
    await flushPromises()
    expect(createToken).toHaveBeenCalledWith('ci', expect.anything(), null, 'pw')
    expect(dialog(w).exists()).toBe(false)
    expect(w.text()).toContain('fh_x_y')
  })

  it('a wrong password stays in the dialog', async () => {
    createToken.mockRejectedValue({
      isAxiosError: true,
      response: { status: 403, data: { code: 'INVALID_PASSWORD', error: 'Password incorrect.' } },
    })
    const w = makeWrapper()
    await flushPromises()
    await fillAndSubmit(w)
    await w.find('[data-testid="step-up-password"]').setValue('nope')
    await dialog(w).find('form').trigger('submit')
    await flushPromises()
    expect(dialog(w).exists()).toBe(true)
    expect(dialog(w).find('[role="alert"]').exists()).toBe(true)
  })
})

// A token an admin switched off looked like any other in its owner's list.
describe('ApiTokenPanel list', () => {
  const item = (id: number, name: string, disabled: boolean) => ({
    id,
    name,
    last4: 'abcd',
    created_at: '2026-11-01T10:00:00',
    last_used_at: null,
    expires_at: null,
    scopes: null,
    disabled,
  })

  it('marks a token an administrator switched off, and only that one', async () => {
    listTokens.mockResolvedValueOnce({
      data: { items: [item(1, 'backup-script', true), item(2, 'laptop', false)], can_create: true },
    })
    const w = makeWrapper()
    await flushPromises()

    const rows = w.findAll('.token-row')
    expect(rows.map((r) => r.find('.token-name').text().split(/\s+/)[0])).toEqual([
      'backup-script',
      'laptop',
    ])
    expect(rows[0].find('[data-testid="token-disabled"]').text()).toBe(
      'Switched off by an administrator',
    )
    expect(rows[1].find('[data-testid="token-disabled"]').exists()).toBe(false)
  })
})
