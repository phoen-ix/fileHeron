/* A typed address that was never picked looked exactly like a picked one: the
 * results list only rendered when it had rows, so its own "No matches." line
 * could never show, and the form's submit stayed grey with nothing on screen
 * saying why (2026-09-28). These pin what the picker now says, and the
 * "send a download link" row for an address with no account. */
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({
  searchUsers: vi.fn(),
  listRecipientTargetGroups: vi.fn(),
}))
vi.mock('@/api/users', () => ({ searchUsers: (q: string) => api.searchUsers(q) }))
vi.mock('@/api/groups', () => ({
  listRecipientTargetGroups: () => api.listRecipientTargetGroups(),
}))

import RecipientPicker from '@/components/RecipientPicker.vue'

const ANNA = {
  user_id: 7,
  display_name: 'Anna Client',
  email: 'anna@client.test',
  role: 'client',
}

function mountPicker(props: Record<string, unknown> = {}) {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(RecipientPicker, {
    props: { modelValue: { user_ids: [], group_ids: [], emails: [] }, ...props },
    global: { plugins: [i18n] },
  })
}

/** Type, let the 180ms debounce and the search settle, then optionally blur
 *  (the list closes 120ms after blur). */
async function type(w: ReturnType<typeof mountPicker>, text: string, { blur = false } = {}) {
  const input = w.find('input')
  await input.trigger('focus')
  await input.setValue(text)
  vi.advanceTimersByTime(200)
  await flushPromises()
  if (blur) {
    await input.trigger('blur')
    vi.advanceTimersByTime(150)
    await flushPromises()
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  api.searchUsers.mockReset()
  api.listRecipientTargetGroups.mockReset()
  api.listRecipientTargetGroups.mockResolvedValue({ data: { items: [] } })
  api.searchUsers.mockImplementation(async (q: string) => ({
    data: { items: q && ANNA.email.includes(q.toLowerCase()) ? [ANNA] : [] },
  }))
})

afterEach(() => {
  vi.useRealTimers()
})

describe('RecipientPicker', () => {
  it('says so when typed text was never picked', async () => {
    const w = mountPicker()
    await type(w, 'anna', { blur: true })

    const pending = w.find('[data-testid="recipient-pending"]')
    expect(pending.exists()).toBe(true)
    expect(pending.text()).toContain('isn\'t added yet')
    expect(w.emitted('update:pending')?.at(-1)).toEqual(['anna'])
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  it('names an address that matches no one, and points at the public link', async () => {
    const w = mountPicker({ canPublicLink: true })
    await type(w, 'michael@elsewhere.test')

    expect(w.find('.results-empty').text()).toContain('No one you can send to has the address')
    expect(w.find('.results-empty').text()).toContain('attach a public link')
    expect(w.find('[data-testid="external-option"]').exists()).toBe(false)

    await w.find('input').trigger('blur')
    vi.advanceTimersByTime(150)
    await flushPromises()
    expect(w.find('[data-testid="recipient-pending"]').text()).toContain(
      'michael@elsewhere.test',
    )
  })

  it('offers a download link to an unknown address only when allowed', async () => {
    const w = mountPicker({ allowExternal: true })
    await type(w, 'Michael@Elsewhere.test')

    const row = w.find('[data-testid="external-option"]')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain('michael@elsewhere.test')

    await row.trigger('mousedown')
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([
      { user_ids: [], group_ids: [], emails: ['michael@elsewhere.test'] },
    ])
    expect(w.emitted('update:pending')?.at(-1)).toEqual([''])
    expect(w.text()).toContain('michael@elsewhere.test')
  })

  it('never offers a link to an address that belongs to someone listed', async () => {
    const w = mountPicker({ allowExternal: true })
    await type(w, 'anna@client.test')

    expect(w.find('[data-testid="external-option"]').exists()).toBe(false)
    expect(w.text()).toContain('Anna Client')
  })

  it('does not offer a link before the search for that text has answered', async () => {
    const w = mountPicker({ allowExternal: true })
    const input = w.find('input')
    await input.trigger('focus')
    await input.setValue('anna@client.test')
    // Debounce not yet elapsed: the list still answers the previous query.
    vi.advanceTimersByTime(50)
    await flushPromises()
    expect(w.find('[data-testid="external-option"]').exists()).toBe(false)
  })
})
