/* The share page's public-link panel. A link's URL is not a one-time secret:
 * the token is stored encrypted, so the panel shows it (with Copy and a QR
 * code) for as long as the link lives - including right after creating it,
 * where it used to say "It will not be shown again" behind an "I've saved the
 * URL" button (2026-09-28). */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({
  getPublicLink: vi.fn(),
  createPublicLink: vi.fn(),
  revokePublicLink: vi.fn(),
  updatePublicLink: vi.fn(),
}))
vi.mock('@/api/publicLinks', () => ({
  getPublicLink: (id: string) => api.getPublicLink(id),
  createPublicLink: (id: string, p: unknown) => api.createPublicLink(id, p),
  revokePublicLink: (id: string) => api.revokePublicLink(id),
  updatePublicLink: (id: string, o: unknown) => api.updatePublicLink(id, o),
}))

import PublicLinkPanel from '@/components/PublicLinkPanel.vue'
import { useUiStore } from '@/stores/ui'

const URL_ = 'https://files.example.test/d/abc123'

function stored(over: Record<string, unknown> = {}) {
  return {
    id: 'pl1',
    url: URL_,
    qr_svg: '<svg></svg>',
    download_limit: null,
    downloads_remaining: null,
    notify_on_download: false,
    has_password: false,
    locked_until: null,
    revoked_at: null,
    created_at: '2026-09-28T08:00:00',
    ...over,
  }
}

async function mountPanel() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const w = mount(PublicLinkPanel, { props: { shareId: 's1' }, global: { plugins: [i18n] } })
  await flushPromises()
  return w
}

beforeEach(() => {
  setActivePinia(createPinia())
  api.getPublicLink.mockReset()
  api.createPublicLink.mockReset()
  api.updatePublicLink.mockReset()
})

describe('PublicLinkPanel', () => {
  it('shows a stored link again, with Copy', async () => {
    api.getPublicLink.mockResolvedValue({ data: stored() })
    const w = await mountPanel()

    expect(w.find('.active-card .url').text()).toBe(URL_)
    expect(w.text()).toContain(en.public_link.url_copy)
  })

  it('shows the new link on the normal card right after creating it', async () => {
    api.getPublicLink.mockRejectedValue(new Error('404'))
    const { url: _u, locked_until: _l, revoked_at: _r, ...created } = stored()
    api.createPublicLink.mockResolvedValue({ data: { ...created, url: URL_ } })
    const w = await mountPanel()
    const toast = vi.spyOn(useUiStore(), 'pushToast')

    await w.find('form.create-form').trigger('submit')
    await flushPromises()

    expect(w.find('.active-card .url').text()).toBe(URL_)
    expect(w.text()).toContain(en.public_link.url_copy)
    expect(w.text()).not.toMatch(/shown again|I've saved/i)
    expect(toast).toHaveBeenCalledWith(en.public_link.created_toast, 'success')
  })

  it('explains a legacy link whose URL was never stored', async () => {
    api.getPublicLink.mockResolvedValue({ data: stored({ url: null, qr_svg: null }) })
    const w = await mountPanel()

    expect(w.find('.active-card .url').exists()).toBe(false)
    expect(w.text()).toContain(en.public_link.url_legacy_hint)
  })

  /* A used-up link could only be revoked and recreated - a NEW address, which
   * killed the one already mailed (2026-11-20). */
  it('raises the limit of a used-up link and keeps its address', async () => {
    api.getPublicLink.mockResolvedValue({
      data: stored({ download_limit: 2, downloads_remaining: 0 }),
    })
    api.updatePublicLink.mockResolvedValue({
      data: stored({ download_limit: 5, downloads_remaining: 3 }),
    })
    const w = await mountPanel()
    const toast = vi.spyOn(useUiStore(), 'pushToast')
    expect(w.text()).toContain('2 of 2 used')

    await w.find('[data-testid="public-link-edit-limit"]').trigger('click')
    await w.find('.limit-editor input[type="number"]').setValue(5)
    await w.find('.limit-editor .fh-btn').trigger('click')
    await flushPromises()

    expect(api.updatePublicLink).toHaveBeenCalledWith('s1', { limit: 5, clear: false })
    expect(w.text()).toContain('2 of 5 used')
    expect(w.find('.active-card .url').text()).toBe(URL_)
    expect(w.find('.limit-editor').exists()).toBe(false)
    expect(toast).toHaveBeenCalledWith(en.public_link.limit_saved_toast, 'success')
  })

  it('removes the limit', async () => {
    api.getPublicLink.mockResolvedValue({
      data: stored({ download_limit: 2, downloads_remaining: 1 }),
    })
    api.updatePublicLink.mockResolvedValue({ data: stored() })
    const w = await mountPanel()

    await w.find('[data-testid="public-link-edit-limit"]').trigger('click')
    await w.find('.limit-editor input[type="checkbox"]').setValue(true)
    expect(w.find('.limit-editor input[type="number"]').exists()).toBe(false)
    await w.find('.limit-editor .fh-btn').trigger('click')
    await flushPromises()

    expect(api.updatePublicLink).toHaveBeenCalledWith('s1', { limit: 2, clear: true })
    expect(w.text()).toContain(en.public_link.limit_none)
  })
})
