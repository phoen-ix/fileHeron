import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'

import en from '@/i18n/locales/en.json'

const SUMMARY = {
  locales: [
    { code: 'en', label: 'English' },
    { code: 'de', label: 'Deutsch' },
  ],
  groups: ['shares', 'security'],
  items: [
    { slug: 'share_created', group: 'shares', has_override: { en: false, de: false } },
    { slug: 'reset_password', group: 'security', has_override: { en: true, de: false } },
  ],
  placeholders: {
    share_created: [{ token: '[SENDER]', label: 'Sender', description: 'x', kind: 'text', required: false }],
    reset_password: [{ token: '[RESET_LINK]', label: 'Reset link', description: 'x', kind: 'url', required: true }],
  },
}

function detail(slug: string, locale: string, hasOverride = false) {
  return {
    data: {
      slug,
      group: slug === 'share_created' ? 'shares' : 'security',
      locale,
      has_override: hasOverride,
      subject: 'Built-in subject',
      body_markdown: hasOverride ? 'Custom body' : '',
      default_subject: 'Built-in subject',
      default_body: 'Default body for [SENDER]',
      placeholders: SUMMARY.placeholders[slug as keyof typeof SUMMARY.placeholders],
    },
  }
}

const getEmailTemplates = vi.fn(async () => ({ data: SUMMARY }))
const getEmailTemplate = vi.fn(async (slug: string, locale: string) =>
  detail(slug, locale, SUMMARY.items.find((i) => i.slug === slug)?.has_override[locale] ?? false),
)
const updateEmailTemplate = vi.fn(async (slug: string, locale: string) => detail(slug, locale, true))
const resetEmailTemplate = vi.fn(async (slug: string, locale: string) => detail(slug, locale, false))
const previewEmailTemplate = vi.fn(async () => ({ data: { subject: 'S', text: 'T', html: '<p>H</p>' } }))
const testSendEmailTemplate = vi.fn(async () => ({ data: { ok: true, sent_to: 'admin@x', error_class: null, error_message: null, smtp_code: null, hint: null } }))

vi.mock('@/api/admin', () => ({
  getEmailTemplates: () => getEmailTemplates(),
  getEmailTemplate: (s: string, l: string) => getEmailTemplate(s, l),
  updateEmailTemplate: (s: string, l: string) => updateEmailTemplate(s, l),
  resetEmailTemplate: (s: string, l: string) => resetEmailTemplate(s, l),
  previewEmailTemplate: (s: string, l: string) => previewEmailTemplate(s, l),
  testSendEmailTemplate: (s: string, l: string) => testSendEmailTemplate(s, l),
}))

vi.mock('vue-router', () => ({ onBeforeRouteLeave: () => {} }))

const pushToast = vi.fn()
const confirm = vi.fn(async () => true)
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast, confirm }) }))

const MarkdownEditorStub = defineComponent({
  name: 'MarkdownEditor',
  props: ['modelValue', 'placeholders', 'disabled', 'ariaLabel'],
  emits: ['update:modelValue', 'ready'],
  setup(props, { emit, expose }) {
    expose({ insertText: vi.fn(), focus: vi.fn() })
    return () =>
      h('textarea', {
        class: 'md-stub',
        value: props.modelValue,
        onInput: (e: Event) => emit('update:modelValue', (e.target as HTMLTextAreaElement).value),
      })
  },
})

import AdminSettingsEmailTemplates from '@/views/AdminSettingsEmailTemplates.vue'

function makeWrapper() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(AdminSettingsEmailTemplates, {
    global: { plugins: [i18n], stubs: { MarkdownEditor: MarkdownEditorStub } },
  })
}

function btnByText(w: ReturnType<typeof makeWrapper>, text: string) {
  return w.findAll('button').find((b) => b.text().trim() === text)!
}

describe('AdminSettingsEmailTemplates', () => {
  beforeEach(() => {
    getEmailTemplates.mockClear()
    getEmailTemplate.mockClear()
    updateEmailTemplate.mockClear()
    resetEmailTemplate.mockClear()
    previewEmailTemplate.mockClear()
    testSendEmailTemplate.mockClear()
    pushToast.mockClear()
  })

  it('loads the summary and renders groups, templates, locale tabs', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(getEmailTemplates).toHaveBeenCalled()
    expect(w.text()).toContain('Shares & files')
    expect(w.text()).toContain('Share created')
    expect(w.text()).toContain('English')
    expect(w.text()).toContain('Deutsch')
    // First template loaded; built-in → Default badge.
    expect(w.text()).toContain('Default')
  })

  it('selecting a template fetches its detail', async () => {
    const w = makeWrapper()
    await flushPromises()
    getEmailTemplate.mockClear()
    await btnByText(w, 'Password reset').trigger('click')
    await flushPromises()
    expect(getEmailTemplate).toHaveBeenCalledWith('reset_password', 'en')
  })

  it('Save sends the current subject and body', async () => {
    const w = makeWrapper()
    await flushPromises()
    await w.find('input[type="text"]').setValue('Edited subject')
    await btnByText(w, 'Save').trigger('click')
    await flushPromises()
    expect(updateEmailTemplate).toHaveBeenCalledWith('share_created', 'en')
    expect(pushToast).toHaveBeenCalled()
  })

  it('Preview renders the returned HTML in a sandboxed iframe', async () => {
    const w = makeWrapper()
    await flushPromises()
    await btnByText(w, 'Preview').trigger('click')
    await flushPromises()
    const frame = w.find('iframe.preview-frame')
    expect(frame.exists()).toBe(true)
    expect(frame.attributes('sandbox')).toBe('')
    expect(frame.attributes('srcdoc')).toContain('<p>H</p>')
  })

  it('Reset confirms then deletes the override', async () => {
    const w = makeWrapper()
    await flushPromises()
    // Move to the customized template so Reset is enabled.
    await btnByText(w, 'Password reset').trigger('click')
    await flushPromises()
    await btnByText(w, 'Reset to default').trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalled()
    expect(resetEmailTemplate).toHaveBeenCalledWith('reset_password', 'en')
  })
})
