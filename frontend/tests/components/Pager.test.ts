/* The audit, mail and error logs accept page <= 1000 (`le=1000`); the pager
 * offered page 1001 and the "Next" click came back 422. */
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import en from '@/i18n/locales/en.json'
import Pager from '@/components/Pager.vue'

function pager(props: Record<string, number>) {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(Pager, { props: props as never, global: { plugins: [i18n] } })
}

describe('Pager', () => {
  it('stops at the server page ceiling', () => {
    const w = pager({ page: 1000, total: 60_000, pageSize: 50, maxPage: 1000 })
    expect(w.text()).toContain('1000')
    expect(w.text()).not.toContain(en.admin_users.next)
  })

  it('is unchanged without one', () => {
    const w = pager({ page: 1000, total: 60_000, pageSize: 50 })
    expect(w.text()).toContain(en.admin_users.next)
  })
})
