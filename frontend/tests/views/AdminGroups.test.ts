/* The create-group form's Cancel only hid it: reopening showed the name that
 * had been refused, under the refusal (found 2026-11-21 while looking for
 * "the second time" defects after the recipient picker's). */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({ listGroups: vi.fn(), createGroup: vi.fn() }))
vi.mock('@/api/groups', () => ({
  listGroups: () => api.listGroups(),
  createGroup: (body: unknown) => api.createGroup(body),
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

import AdminGroups from '@/views/AdminGroups.vue'

async function mountPage() {
  setActivePinia(createPinia())
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(AdminGroups, {
    global: {
      plugins: [i18n],
      stubs: {
        AdminPageHeader: { template: '<header><slot name="actions" /></header>' },
        RouterLink: { template: '<a><slot /></a>' },
      },
    },
  })
  await flushPromises()
  return w
}

beforeEach(() => {
  api.listGroups.mockReset().mockResolvedValue({ data: { items: [] } })
  api.createGroup.mockReset()
})

describe('AdminGroups create form', () => {
  const byText = (w: Awaited<ReturnType<typeof mountPage>>, text: string) =>
    w.findAll('button').find((b) => b.text().startsWith(text))!

  it('opens clean after a refused name was cancelled', async () => {
    api.createGroup.mockRejectedValueOnce(
      Object.assign(new Error('GROUP_EXISTS'), {
        isAxiosError: true,
        response: { status: 409, data: { code: 'GROUP_EXISTS', error: 'A group has that name.' } },
      }),
    )
    const w = await mountPage()
    await byText(w, en.admin_groups.new_group).trigger('click')
    await w.find('.create-form input[type="text"]').setValue('Design')
    await w.find('.create-form').trigger('submit')
    await flushPromises()
    expect(w.find('.create-form [role="alert"]').exists()).toBe(true)

    await byText(w, en.common.cancel).trigger('click')
    await byText(w, en.admin_groups.new_group).trigger('click')
    expect(w.find('.create-form [role="alert"]').exists()).toBe(false)
    expect((w.find('.create-form input[type="text"]').element as HTMLInputElement).value).toBe('')
  })

  it('creates two groups in a row', async () => {
    api.createGroup.mockResolvedValue({ data: {} })
    const w = await mountPage()
    for (const name of ['Design', 'Legal']) {
      await byText(w, en.admin_groups.new_group).trigger('click')
      await w.find('.create-form input[type="text"]').setValue(name)
      await w.find('.create-form').trigger('submit')
      await flushPromises()
    }
    expect(api.createGroup.mock.calls.map((c) => (c[0] as { name: string }).name)).toEqual([
      'Design',
      'Legal',
    ])
  })
})
