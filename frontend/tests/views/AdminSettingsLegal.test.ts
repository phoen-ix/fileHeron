/* The Legal pages tab (split out of Branding & legal): one language shown at a
 * time with every editor kept mounted, a switch that says which language is
 * active (aria-pressed - it is an in-page toggle, not a tablist), and a save
 * that sends every language whichever is showing. */
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const LEGAL = {
  imprint: { enabled: false, en: '', de: '' },
  privacy: { enabled: false, en: '', de: '' },
}
const api = vi.hoisted(() => ({ getLegalSettings: vi.fn(), updateLegalSettings: vi.fn() }))
vi.mock('@/api/admin', () => ({
  getLegalSettings: () => api.getLegalSettings(),
  updateLegalSettings: (p: unknown) => api.updateLegalSettings(p),
}))
const pushToast = vi.fn()
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast }) }))
const loadConfig = vi.fn(async () => {})
vi.mock('@/stores/site', () => ({ useSiteStore: () => ({ loadConfig }) }))

const RichTextEditorStub = defineComponent({
  name: 'RichTextEditor',
  props: { modelValue: { type: String, default: '' }, ariaLabel: { type: String, default: '' } },
  emits: ['update:modelValue'],
  setup: (props) => () => h('textarea', { class: 'md-stub', value: props.modelValue }),
})

import AdminSettingsLegal from '@/views/AdminSettingsLegal.vue'

function mountPage() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminSettingsLegal, {
    global: { plugins: [i18n], stubs: { RichTextEditor: RichTextEditorStub } },
  })
}

beforeEach(() => {
  api.getLegalSettings.mockReset().mockResolvedValue({ data: structuredClone(LEGAL) })
  api.updateLegalSettings.mockReset().mockResolvedValue({ data: structuredClone(LEGAL) })
  pushToast.mockClear()
  loadConfig.mockClear()
})

describe('AdminSettingsLegal', () => {
  it('shows one language at a time and says which', async () => {
    const w = mountPage()
    await flushPromises()
    const opts = w.findAll('.locale-option')
    expect(opts.map((b) => b.text())).toEqual(['English', 'German'])
    expect(opts.map((b) => b.attributes('aria-pressed'))).toEqual(['true', 'false'])

    // All four editors stay mounted (imprint/privacy x en/de); only the active
    // language's two are shown.
    const langs = w.findAll('.legal-lang')
    expect(langs.length).toBe(4)
    const hidden = () =>
      langs.filter((l) => (l.element as HTMLElement).style.display === 'none').length
    expect(hidden()).toBe(2)

    await opts[1].trigger('click')
    expect(opts.map((b) => b.attributes('aria-pressed'))).toEqual(['false', 'true'])
    expect(hidden()).toBe(2)
  })

  it('is a group of toggles, not a fake tablist', async () => {
    const w = mountPage()
    await flushPromises()
    expect(w.find('[role="tablist"]').exists()).toBe(false)
    expect(w.find('.locale-switch').attributes('role')).toBe('group')
  })

  it('saves every language regardless of the one showing', async () => {
    const w = mountPage()
    await flushPromises()
    await w.findAll('.locale-option')[1].trigger('click')
    await w.find('.actions button').trigger('click')
    await flushPromises()
    expect(api.updateLegalSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        imprint: expect.objectContaining({ en: expect.any(String), de: expect.any(String) }),
        privacy: expect.objectContaining({ en: expect.any(String), de: expect.any(String) }),
      }),
    )
    expect(loadConfig).toHaveBeenCalled()
    expect(pushToast).toHaveBeenCalledWith(en.common.saved, 'success')
  })
})
