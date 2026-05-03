import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import SectionQuickNav from '@/components/SectionQuickNav.vue'
import en from '@/i18n/locales/en.json'

function makeWrapper(active: string, ariaLabel = 'Quick navigation') {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    fallbackLocale: 'en',
    messages: { en },
  })
  return mount(SectionQuickNav, {
    global: { plugins: [i18n] },
    props: {
      sections: [
        { id: 'profile', labelKey: 'account.section_profile' },
        { id: 'password', labelKey: 'account.section_password' },
        { id: 'sessions', labelKey: 'account.section_sessions' },
      ],
      active,
      ariaLabel,
    },
  })
}

describe('SectionQuickNav', () => {
  it('renders one button per section using the i18n labels', () => {
    const w = makeWrapper('profile')
    const buttons = w.findAll('button.quicknav-item')
    expect(buttons).toHaveLength(3)
    expect(buttons[0].text()).toBe('Profile')
    expect(buttons[1].text()).toBe('Password')
    expect(buttons[2].text()).toBe('Active sessions')
  })

  it('marks only the active section with is-active + aria-current', () => {
    const w = makeWrapper('password')
    const buttons = w.findAll('button.quicknav-item')
    expect(buttons[0].classes()).not.toContain('is-active')
    expect(buttons[1].classes()).toContain('is-active')
    expect(buttons[1].attributes('aria-current')).toBe('true')
    expect(buttons[2].attributes('aria-current')).toBeUndefined()
  })

  it('emits jump with the section id when a button is clicked', async () => {
    const w = makeWrapper('profile')
    await w.findAll('button.quicknav-item')[2].trigger('click')
    expect(w.emitted('jump')).toEqual([['sessions']])
  })

  it('uses the ariaLabel prop on the nav landmark', () => {
    const w = makeWrapper('profile', 'Section navigation')
    expect(w.find('nav').attributes('aria-label')).toBe('Section navigation')
  })
})
