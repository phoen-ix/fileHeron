/* A typed address that was never picked looked exactly like a picked one: the
 * results list only rendered when it had rows, so its own "No matches." line
 * could never show, and the form's submit stayed grey with nothing on screen
 * saying why (2026-09-28). These pin what the picker now says, and the
 * "send a download link" row for an address with no account. */
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

import { click, clickAway, press, type as typeInto } from '../helpers/user'

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

const mounted: { unmount: () => void }[] = []

// Attached to the document: only an element in it can take the focus, and the
// focus is what these tests are about (tests/helpers/user.ts).
function mountPicker(props: Record<string, unknown> = {}) {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(RecipientPicker, {
    attachTo: document.body,
    props: { modelValue: { user_ids: [], group_ids: [], emails: [] }, ...props },
    global: {
      plugins: [i18n],
      stubs: { RouterLink: { props: ['to'], template: '<a class="router-link"><slot /></a>' } },
    },
  })
  mounted.push(w)
  return w
}

/** Let the 180ms debounce and the search settle. */
async function settle() {
  vi.advanceTimersByTime(200)
  await flushPromises()
}

/** Click away and outwait the list's 120ms close. */
async function leave() {
  await clickAway()
  vi.advanceTimersByTime(150)
  await flushPromises()
}

/** Type as a user does - the field takes the focus only if it lacks it - and
 *  let the search settle; optionally click away afterwards. */
async function type(w: ReturnType<typeof mountPicker>, text: string, { blur = false } = {}) {
  await typeInto(w.find('input'), text)
  await settle()
  if (blur) await leave()
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
  mounted.splice(0).forEach((w) => w.unmount())
  vi.useRealTimers()
})

describe('RecipientPicker', () => {
  it('says so when typed text was never picked', async () => {
    const w = mountPicker()
    await type(w, 'anna', { blur: true })

    const pending = w.find('[data-testid="recipient-pending"]')
    expect(pending.exists()).toBe(true)
    expect(pending.text()).toContain("isn't added yet")
    expect(w.emitted('update:pending')?.at(-1)).toEqual([{ text: 'anna', noAccount: false }])
    expect(w.emitted('update:modelValue')).toBeUndefined()
  })

  it('tells staff what they can do with an address that matches no one', async () => {
    const w = mountPicker({ canPublicLink: true })
    await type(w, 'michael@elsewhere.test')

    const empty = w.find('.results-empty').text()
    expect(empty).toContain('No one you can send to has the address')
    expect(empty).toContain(en.recipient.no_account_action)
    expect(w.find('[data-testid="external-option"]').exists()).toBe(false)
    expect(w.emitted('update:pending')?.at(-1)).toEqual([
      { text: 'michael@elsewhere.test', noAccount: true },
    ])

    await leave()
    const pending = w.find('[data-testid="recipient-pending"]')
    expect(pending.text()).toContain('michael@elsewhere.test')
    expect(pending.text()).toContain(en.recipient.no_account_action)
    expect(pending.find('.router-link').exists()).toBe(false)
  })

  it('shows an admin the switch that would make the address addable', async () => {
    const w = mountPicker({ canPublicLink: true, isAdmin: true })
    await type(w, 'michael@elsewhere.test', { blur: true })

    const pending = w.find('[data-testid="recipient-pending"]')
    expect(pending.text()).toContain('No account has the address michael@elsewhere.test')
    const link = pending.find('.router-link')
    expect(link.exists()).toBe(true)
    expect(link.text()).toBe(en.recipient.external_setting_name)
    expect(pending.text()).not.toContain(en.recipient.no_account_action)
  })

  it('clears the typed text in one click, which clears the blocker', async () => {
    const w = mountPicker({ canPublicLink: true })
    await type(w, 'michael@elsewhere.test', { blur: true })

    await click(w.find('[data-testid="recipient-clear"]'))
    await flushPromises()

    expect((w.find('input').element as HTMLInputElement).value).toBe('')
    expect(w.find('[data-testid="recipient-pending"]').exists()).toBe(false)
    expect(w.emitted('update:pending')?.at(-1)).toEqual([{ text: '', noAccount: false }])
  })

  it('offers a download link to an unknown address only when allowed', async () => {
    const w = mountPicker({ allowExternal: true })
    await type(w, 'Michael@Elsewhere.test')

    const row = w.find('[data-testid="external-option"]')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain('michael@elsewhere.test')

    await click(row)
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([
      { user_ids: [], group_ids: [], emails: ['michael@elsewhere.test'] },
    ])
    expect(w.emitted('update:pending')?.at(-1)).toEqual([{ text: '', noAccount: false }])
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
    await typeInto(w.find('input'), 'anna@client.test')
    // Debounce not yet elapsed: the list still answers the previous query.
    vi.advanceTimersByTime(50)
    await flushPromises()
    expect(w.find('[data-testid="external-option"]').exists()).toBe(false)
  })

  it('labels the field for whom a secret request asks', () => {
    expect(mountPicker().find('label').text()).toBe(en.recipient.label)
    expect(mountPicker({ purpose: 'request' }).find('label').text()).toBe(
      en.recipient.request.label,
    )
  })
})

/* A pick closed the list while the field kept the focus, and only `@focus` (or
 * an arrow key) opened it again: the second address sat in the box as "not
 * added yet - pick it from the list" with no list, and the form would not
 * submit (2026-11-20). Every step here is one a user takes: the field is
 * clicked once, and nothing fires a focus the browser would not. */
describe('RecipientPicker with several recipients', () => {
  const BEN = { user_id: 8, display_name: 'Ben Client', email: 'ben@client.test', role: 'client' }
  const RIVERSIDE = {
    id: 3,
    name: 'Project Riverside',
    description: null,
    is_company_inbox: false,
    member_count: 3,
  }

  /** As for an admin: the empty query lists everyone. */
  function everyoneListed() {
    api.searchUsers.mockImplementation(async (q: string) => ({
      data: {
        items: [ANNA, BEN].filter((u) => !q || u.email.includes(q.toLowerCase())),
      },
    }))
  }

  const listOpen = (w: ReturnType<typeof mountPicker>) => w.find('.results').exists()
  const option = (w: ReturnType<typeof mountPicker>, text: string) =>
    w.findAll('.result-row').find((r) => r.text().includes(text))!

  it('adds a second and a third recipient without leaving the field', async () => {
    const w = mountPicker({ allowExternal: true })
    await type(w, 'first@elsewhere.test')
    await click(w.find('[data-testid="external-option"]'))

    await type(w, 'second@elsewhere.test')
    const row = w.find('[data-testid="external-option"]')
    expect(row.exists()).toBe(true)
    expect(w.find('[data-testid="recipient-pending"]').exists()).toBe(false)
    await click(row)

    await type(w, 'anna')
    await press('Enter')

    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([
      {
        user_ids: [ANNA.user_id],
        group_ids: [],
        emails: ['first@elsewhere.test', 'second@elsewhere.test'],
      },
    ])
  })

  it('adds two people and a group by mouse alone', async () => {
    everyoneListed()
    api.listRecipientTargetGroups.mockResolvedValue({ data: { items: [RIVERSIDE] } })
    const w = mountPicker()
    const input = w.find('input').element

    await type(w, 'anna')
    await click(option(w, 'Anna Client'))
    expect(document.activeElement).toBe(input)

    await type(w, 'ben')
    await click(option(w, 'Ben Client'))

    await type(w, 'River')
    await click(option(w, 'Project Riverside'))

    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([
      { user_ids: [ANNA.user_id, BEN.user_id], group_ids: [RIVERSIDE.id], emails: [] },
    ])
    expect(w.findAll('.chip')).toHaveLength(3)
  })

  it('ignores Enter until the search for the typed text has answered', async () => {
    everyoneListed()
    const w = mountPicker({ allowExternal: true })
    await type(w, 'first@elsewhere.test')
    await click(w.find('[data-testid="external-option"]'))
    await settle()

    await typeInto(w.find('input'), 'second@elsewhere.test')
    await press('Enter')
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([
      { user_ids: [], group_ids: [], emails: ['first@elsewhere.test'] },
    ])

    await settle()
    await press('Enter')
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([
      { user_ids: [], group_ids: [], emails: ['first@elsewhere.test', 'second@elsewhere.test'] },
    ])
  })

  it('opens the list again when the user types after Escape', async () => {
    everyoneListed()
    const w = mountPicker()
    await type(w, 'anna')
    await press('Escape')
    expect(listOpen(w)).toBe(false)

    await type(w, 'ben')
    expect(option(w, 'Ben Client').exists()).toBe(true)
  })

  // Pressing a label takes the focus off its field and releasing it gives the
  // focus back: blur, then focus, usually less than 120ms apart. The blur's
  // delayed close then ran under a focused field.
  it('keeps the list open when the field is left and re-entered at once', async () => {
    everyoneListed()
    const w = mountPicker()
    await settle()
    await click(w.find('input'))
    expect(listOpen(w)).toBe(true)

    await click(w.find('label'))
    expect(document.activeElement).toBe(w.find('input').element)
    vi.advanceTimersByTime(150)
    await flushPromises()
    expect(listOpen(w)).toBe(true)

    await clickAway()
    vi.advanceTimersByTime(60)
    await click(w.find('input'))
    vi.advanceTimersByTime(150)
    await flushPromises()
    expect(listOpen(w)).toBe(true)
  })

  it('closes the list once the field is really left', async () => {
    everyoneListed()
    const w = mountPicker()
    await settle()
    await click(w.find('input'))
    await leave()
    expect(listOpen(w)).toBe(false)
  })
})

describe('RecipientPicker groups', () => {
  const RIVERSIDE = {
    id: 3,
    name: 'Project Riverside',
    description: null,
    is_company_inbox: false,
    member_count: 3,
  }

  beforeEach(() => {
    api.listRecipientTargetGroups.mockResolvedValue({ data: { items: [RIVERSIDE] } })
  })

  // Vue casts an ABSENT boolean prop to false, not undefined. The flag was an
  // `allowGroups` checked as `=== false`, which held on every form that did
  // not pass it - the new-share form showed "No matches." for every group
  // (found by the docs tour, 2026-10-02). Hence the negative `hideGroups`.
  it('offers groups when no flag is passed, as on the new-share form', async () => {
    const w = mountPicker()
    await flushPromises()
    expect(api.listRecipientTargetGroups).toHaveBeenCalledTimes(1)
    await type(w, 'River')
    const rows = w.findAll('.result-row').map((r) => r.text())
    expect(rows.some((t) => t.includes('Project Riverside'))).toBe(true)
  })

  it('neither asks for nor offers groups with hide-groups', async () => {
    const w = mountPicker({ hideGroups: true })
    await flushPromises()
    expect(api.listRecipientTargetGroups).not.toHaveBeenCalled()
    await type(w, 'River')
    expect(w.findAll('.result-row').some((r) => r.text().includes('Project Riverside'))).toBe(false)
  })
})
