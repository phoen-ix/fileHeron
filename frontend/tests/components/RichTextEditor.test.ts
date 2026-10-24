/* The editor debounces its emit by 150 ms, and on the email-template page ONE
 * editor serves every template. A keystroke followed by switching template
 * inside that window emitted the OLD template's HTML after the swap - into the
 * newly selected template's body, or skipping the discard prompt. */
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'
import RichTextEditor from '@/components/RichTextEditor.vue'

afterEach(() => {
  vi.useRealTimers()
})

describe('RichTextEditor', () => {
  it('drops a pending emit when the content is replaced', async () => {
    vi.useFakeTimers()
    const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
    const w = mount(RichTextEditor, {
      props: { modelValue: '<p>template A</p>' },
      global: { plugins: [i18n] },
      attachTo: document.body,
    })
    ;(w.vm as unknown as { insertText: (s: string) => void }).insertText(' edited')
    await w.setProps({ modelValue: '<p>template B</p>' }) // switched template
    vi.advanceTimersByTime(300)
    const emitted = (w.emitted('update:modelValue') ?? []).map((e) => String(e[0]))
    expect(emitted.some((html) => html.includes('template A'))).toBe(false)
    w.unmount()
  })

  it('hands a pending edit over at once when the editor loses focus', async () => {
    // The Save button takes focus before its click runs: a save inside the
    // 150 ms window sent the text without the last keystrokes.
    vi.useFakeTimers()
    const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
    const w = mount(RichTextEditor, {
      props: { modelValue: '<p>legal</p>' },
      global: { plugins: [i18n] },
      attachTo: document.body,
    })
    ;(w.vm as unknown as { insertText: (s: string) => void }).insertText(' notice')
    expect(w.emitted('update:modelValue')).toBeUndefined()
    w.find('[role="textbox"]').element.dispatchEvent(new FocusEvent('blur'))
    const emitted = (w.emitted('update:modelValue') ?? []).map((e) => String(e[0]))
    expect(emitted.at(-1)).toContain('notice')
    vi.advanceTimersByTime(300)
    expect(w.emitted('update:modelValue')).toHaveLength(1)
    w.unmount()
  })
})
