/* The first-admin wizard is anonymous until an admin exists, so install.sh
 * prints /setup?token=... and the backend refuses the wizard without it when
 * SETUP_TOKEN is set. The token must reach the POST, leave the address bar, and
 * be askable by hand when the operator arrives without it. */
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const replace = vi.fn(async () => {})
const push = vi.fn(async () => {})
let query: Record<string, string> = {}
vi.mock('vue-router', () => ({
  useRoute: () => ({ query, path: '/setup' }),
  useRouter: () => ({ replace, push }),
}))

let tokenRequired = true
const completeSetup = vi.fn(async (_payload: Record<string, unknown>) => ({
  data: { user_id: 1, email: 'a@b.test' },
}))
vi.mock('@/api/setup', () => ({
  getSetupStatus: vi.fn(async () => ({ data: { required: true, token_required: tokenRequired } })),
  completeSetup: (payload: Record<string, unknown>) => completeSetup(payload),
}))

const login = vi.fn(async () => {})
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ login, setupRequired: true }),
}))

import Setup from '@/views/Setup.vue'

function makeWrapper() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(Setup, { global: { plugins: [i18n], stubs: { AuthCanvas: { template: '<div><slot /></div>' } } } })
}

async function fillAndSubmit(w: ReturnType<typeof makeWrapper>) {
  const inputs = w.findAll('input')
  await inputs[0].setValue('a@b.test')
  await inputs[1].setValue('Admin')
  await inputs[2].setValue('AdminPassword123!')
  await inputs[3].setValue('AdminPassword123!')
  await w.find('form').trigger('submit')
  await flushPromises()
}

describe('Setup', () => {
  beforeEach(() => {
    replace.mockClear()
    completeSetup.mockClear()
    query = {}
    tokenRequired = true
  })

  it('sends the token from the URL and drops it from the address bar', async () => {
    query = { token: 'tok-123' }
    const w = makeWrapper()
    await flushPromises()

    expect(replace).toHaveBeenCalledWith({ query: {} })
    expect(w.text()).not.toContain(en.setup.token_label)
    await fillAndSubmit(w)
    expect(completeSetup.mock.calls[0][0]).toMatchObject({ setup_token: 'tok-123' })
  })

  it('asks for the token when the server wants one and the URL had none', async () => {
    const w = makeWrapper()
    await flushPromises()
    expect(w.text()).toContain(en.setup.token_label)
  })

  it('shows no token field when the server does not want one', async () => {
    tokenRequired = false
    const w = makeWrapper()
    await flushPromises()
    expect(w.text()).not.toContain(en.setup.token_label)
    await fillAndSubmit(w)
    expect(completeSetup.mock.calls[0][0]).toMatchObject({ setup_token: null })
  })
})
