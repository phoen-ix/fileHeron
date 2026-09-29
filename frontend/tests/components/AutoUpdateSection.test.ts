/* The automatic-updates settings block: it asks for the password exactly when
 * the backend will demand it - turning automatic updates on, or changing them
 * while they are on - and never for turning them off. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const getAutoUpdateSettings = vi.fn()
const updateAutoUpdateSettings = vi.fn()

vi.mock('@/api/admin', () => ({
  getAutoUpdateSettings: () => getAutoUpdateSettings(),
  updateAutoUpdateSettings: (p: unknown) => updateAutoUpdateSettings(p),
}))
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast: vi.fn(), confirm: vi.fn() }) }))

import AutoUpdateSection from '@/components/admin/AutoUpdateSection.vue'

function settings(over: Record<string, unknown> = {}) {
  return {
    data: { enabled: false, scope: 'patch', min_age_hours: 24, skipped_tag: null, ...over },
  }
}

async function mountIt() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(AutoUpdateSection, {
    global: { plugins: [i18n], stubs: { RouterLink: true } },
  })
  await flushPromises()
  return w
}

type W = Awaited<ReturnType<typeof mountIt>>
const dialog = (w: W) => w.find('[data-testid="step-up-dialog"]')
const save = (w: W) => w.find<HTMLButtonElement>('[data-testid="auto-update-save"]')

async function submitSettings(w: W) {
  await w.find('form').trigger('submit')
  await flushPromises()
}

async function confirmPassword(w: W, pw: string) {
  await w.find('[data-testid="step-up-password"]').setValue(pw)
  await dialog(w).find('form').trigger('submit')
  await flushPromises()
}

describe('AutoUpdateSection', () => {
  beforeEach(() => {
    getAutoUpdateSettings.mockReset()
    updateAutoUpdateSettings.mockReset()
    updateAutoUpdateSettings.mockImplementation(async (p: Record<string, unknown>) =>
      settings({ ...p, password: undefined }),
    )
  })

  it('shows the shipped defaults and nothing to save', async () => {
    getAutoUpdateSettings.mockResolvedValue(settings())
    const w = await mountIt()
    expect(w.find<HTMLInputElement>('[data-testid="auto-update-enabled"]').element.checked).toBe(
      false,
    )
    expect(w.find<HTMLSelectElement>('[data-testid="auto-update-scope"]').element.value).toBe(
      'patch',
    )
    expect(w.find<HTMLInputElement>('[data-testid="auto-update-min-age"]').element.value).toBe('24')
    expect(w.find('input[type="password"]').exists()).toBe(false)
    expect(save(w).element.disabled).toBe(true)
  })

  it('turning it on asks for the password in a dialog and sends it', async () => {
    getAutoUpdateSettings.mockResolvedValue(settings())
    const w = await mountIt()
    await w.find('[data-testid="auto-update-enabled"]').setValue(true)
    // No inline field: the form itself never holds the password.
    expect(w.find('input[type="password"]').exists()).toBe(false)
    expect(save(w).element.disabled).toBe(false)
    await submitSettings(w)
    expect(updateAutoUpdateSettings).not.toHaveBeenCalled()
    expect(dialog(w).exists()).toBe(true)
    await confirmPassword(w, 'pw')
    expect(updateAutoUpdateSettings).toHaveBeenCalledWith({
      enabled: true,
      scope: 'patch',
      min_age_hours: 24,
      password: 'pw',
    })
    expect(w.emitted('saved')).toHaveLength(1)
    expect(dialog(w).exists()).toBe(false)
  })

  it('changing it while on asks for the password too', async () => {
    getAutoUpdateSettings.mockResolvedValue(settings({ enabled: true }))
    const w = await mountIt()
    await w.find('[data-testid="auto-update-scope"]').setValue('any')
    await submitSettings(w)
    expect(dialog(w).exists()).toBe(true)
    expect(updateAutoUpdateSettings).not.toHaveBeenCalled()
  })

  it('turning it off needs no password', async () => {
    getAutoUpdateSettings.mockResolvedValue(settings({ enabled: true, scope: 'minor' }))
    const w = await mountIt()
    await w.find('[data-testid="auto-update-enabled"]').setValue(false)
    await submitSettings(w)
    expect(dialog(w).exists()).toBe(false)
    expect(updateAutoUpdateSettings).toHaveBeenCalledWith({
      enabled: false,
      scope: 'minor',
      min_age_hours: 24,
    })
  })

  it('shows a wrong password inside the dialog and keeps it open', async () => {
    getAutoUpdateSettings.mockResolvedValue(settings())
    updateAutoUpdateSettings.mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 403,
        data: { error: 'Password is incorrect.', code: 'INVALID_PASSWORD', details: {} },
      },
    })
    const w = await mountIt()
    await w.find('[data-testid="auto-update-enabled"]').setValue(true)
    await submitSettings(w)
    await confirmPassword(w, 'nope')
    expect(dialog(w).find('[role="alert"]').exists()).toBe(true)
    expect(w.emitted('saved')).toBeUndefined()
    expect(dialog(w).exists()).toBe(true)
  })

  it('cancelling the dialog saves nothing', async () => {
    getAutoUpdateSettings.mockResolvedValue(settings())
    const w = await mountIt()
    await w.find('[data-testid="auto-update-enabled"]').setValue(true)
    await submitSettings(w)
    await dialog(w).find('button[type="button"]').trigger('click')
    expect(dialog(w).exists()).toBe(false)
    expect(updateAutoUpdateSettings).not.toHaveBeenCalled()
  })

  it('names a release that failed to install automatically', async () => {
    getAutoUpdateSettings.mockResolvedValue(settings({ enabled: true, skipped_tag: 'v2.19.2' }))
    const w = await mountIt()
    expect(w.find('[data-testid="auto-update-skipped"]').text()).toContain('v2.19.2')
  })
})
