import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const NO_LOGO = {
  logo: { present: false, filename: null, content_type: null, url: null },
  show_header: false, show_login: false, show_public: false, show_email: false,
  show_client: false,
  link_url: null,
}
const WITH_LOGO = { ...NO_LOGO, logo: { present: true, filename: 'l.png', content_type: 'image/png', url: '/api/branding/logo' } }
const LEGAL = {
  imprint: { enabled: false, en: '', de: '' },
  privacy: { enabled: false, en: '', de: '' },
}

const getBrandingSettings = vi.fn(async () => ({ data: { ...NO_LOGO } }))
const updateBrandingSettings = vi.fn(async (_p: unknown) => ({ data: { ...NO_LOGO } }))
const uploadBrandingLogo = vi.fn(async (_f: File) => ({ data: { ...WITH_LOGO } }))
const deleteBrandingLogo = vi.fn(async () => ({ data: { ...NO_LOGO } }))
const getLegalSettings = vi.fn(async () => ({ data: { ...LEGAL } }))
const updateLegalSettings = vi.fn(async (_p: unknown) => ({ data: { ...LEGAL } }))

vi.mock('@/api/admin', () => ({
  getBrandingSettings: () => getBrandingSettings(),
  updateBrandingSettings: (p: unknown) => updateBrandingSettings(p),
  uploadBrandingLogo: (f: File) => uploadBrandingLogo(f),
  deleteBrandingLogo: () => deleteBrandingLogo(),
  getLegalSettings: () => getLegalSettings(),
  updateLegalSettings: (p: unknown) => updateLegalSettings(p),
}))

const pushToast = vi.fn()
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast }) }))

const loadConfig = vi.fn(async () => {})
vi.mock('@/stores/site', () => ({ useSiteStore: () => ({ loadConfig }) }))

const RichTextEditorStub = defineComponent({
  name: 'RichTextEditor',
  props: {
    modelValue: { type: String, default: '' },
    ariaLabel: { type: String, default: '' },
  },
  emits: ['update:modelValue'],
  setup(props) {
    return () => h('textarea', { class: 'md-stub', value: props.modelValue })
  },
})

import AdminSettingsBranding from '@/views/AdminSettingsBranding.vue'

function makeWrapper() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminSettingsBranding, {
    global: { plugins: [i18n], stubs: { RichTextEditor: RichTextEditorStub } },
  })
}

describe('AdminSettingsBranding', () => {
  beforeEach(() => {
    getBrandingSettings.mockClear()
    updateBrandingSettings.mockClear()
    uploadBrandingLogo.mockClear()
    deleteBrandingLogo.mockClear()
    getLegalSettings.mockClear()
    updateLegalSettings.mockClear()
    pushToast.mockClear()
    loadConfig.mockClear()
  })

  it('loads branding + legal on mount', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(getBrandingSettings).toHaveBeenCalled()
    expect(getLegalSettings).toHaveBeenCalled()
    expect(w.text()).toContain('No logo')
  })

  it('saves surface toggles', async () => {
    const w = makeWrapper()
    await flushPromises()
    // First checkbox in the surfaces fieldset = "App header".
    const headerCheck = w.find('.surfaces input[type="checkbox"]')
    await headerCheck.setValue(true)
    await w.findAll('button').find((b) => b.text() === 'Save')!.trigger('click')
    await flushPromises()
    expect(updateBrandingSettings).toHaveBeenCalledWith(
      expect.objectContaining({ show_header: true }),
    )
    expect(loadConfig).toHaveBeenCalled()
  })

  it('saves the desktop-client toggle', async () => {
    const w = makeWrapper()
    await flushPromises()
    const checks = w.findAll('.surfaces input[type="checkbox"]')
    // Order: header, login, public, email, client -> client is the 5th.
    await checks[4].setValue(true)
    await w.findAll('button').find((b) => b.text() === 'Save')!.trigger('click')
    await flushPromises()
    expect(updateBrandingSettings).toHaveBeenCalledWith(
      expect.objectContaining({ show_client: true }),
    )
  })

  it('uploads a logo then offers delete', async () => {
    const w = makeWrapper()
    await flushPromises()
    const input = w.find('input[type="file"]')
    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'logo.png', { type: 'image/png' })
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushPromises()
    expect(uploadBrandingLogo).toHaveBeenCalledWith(file)
    // Delete button now present.
    const del = w.findAll('button').find((b) => b.text() === 'Remove')
    expect(del).toBeTruthy()
    await del!.trigger('click')
    await flushPromises()
    expect(deleteBrandingLogo).toHaveBeenCalled()
  })

  it('rejects a non-image without uploading', async () => {
    const w = makeWrapper()
    await flushPromises()
    const input = w.find('input[type="file"]')
    const file = new File(['x'], 'doc.pdf', { type: 'application/pdf' })
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushPromises()
    expect(uploadBrandingLogo).not.toHaveBeenCalled()
    expect(pushToast).toHaveBeenCalled()
  })

  it('legal editor shows one language at a time via tabs', async () => {
    const w = makeWrapper()
    await flushPromises()
    const tabs = w.findAll('.locale-tab')
    expect(tabs.map((b) => b.text())).toEqual(['English', 'German'])
    expect(tabs[0].classes()).toContain('active') // English default

    // All four editors are mounted (imprint/privacy x en/de) but only the
    // active language's two are shown - v-show sets display:none on the rest.
    const langs = w.findAll('.legal-lang')
    expect(langs.length).toBe(4)
    const hidden = () =>
      langs.filter((l) => (l.element as HTMLElement).style.display === 'none').length
    expect(hidden()).toBe(2) // the German pair is hidden while English is active

    // Switching the tab swaps which two are hidden.
    await tabs[1].trigger('click')
    expect(tabs[1].classes()).toContain('active')
    expect(tabs[0].classes()).not.toContain('active')
    expect(hidden()).toBe(2) // now the English pair is hidden
  })

  it('legal save still sends both languages regardless of the active tab', async () => {
    const w = makeWrapper()
    await flushPromises()
    // Two "Save" buttons: [0] branding, [1] legal.
    const saves = w.findAll('button').filter((b) => b.text() === 'Save')
    await saves[1].trigger('click')
    await flushPromises()
    expect(updateLegalSettings).toHaveBeenCalledWith(
      expect.objectContaining({
        imprint: expect.objectContaining({ en: expect.any(String), de: expect.any(String) }),
        privacy: expect.objectContaining({ en: expect.any(String), de: expect.any(String) }),
      }),
    )
  })
})
