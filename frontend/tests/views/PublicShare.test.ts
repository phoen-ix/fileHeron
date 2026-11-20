/* /d/<token>: the page links to no download. Mail link scanners fetch every URL
 * a mailed page links to, and on 2026-11-20 one spent a two-download link before
 * its recipient had seen the mail. A Download click POSTs for a short-lived URL
 * and only then navigates; a refusal is said on the page. */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import en from '@/i18n/locales/en.json'

const api = vi.hoisted(() => ({
  fetchPublicShare: vi.fn(),
  unlockPublicShare: vi.fn(),
  getPublicDownloadUrl: vi.fn(),
}))
vi.mock('@/api/publicLinks', () => ({
  fetchPublicShare: (t: string) => api.fetchPublicShare(t),
  unlockPublicShare: (t: string, p: string) => api.unlockPublicShare(t, p),
  getPublicDownloadUrl: (t: string, f: string | null) => api.getPublicDownloadUrl(t, f),
  publicPreviewUrl: (t: string, f: string) => `/api/public/${t}/files/${f}/preview`,
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { token: 'tok-1' } }) }))

import PublicShare from '@/views/PublicShare.vue'

const FILE = {
  id: 'f1',
  original_filename: 'Artikelstamm.txt',
  relative_dir: null,
  mime_type: 'text/plain',
  size_bytes: 10,
  state: 'clean',
  av_unscanned: false,
}

function share(over: Record<string, unknown> = {}) {
  return {
    share_id: 's1',
    subject: 'Artikelstamm',
    message: null,
    expires_at: null,
    expires_in_sec: null,
    requires_password: false,
    unlocked: true,
    downloads_remaining: 2,
    preview_enabled: false,
    files: [FILE],
    ...over,
  }
}

const nav = { href: '' }

function mountView() {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(PublicShare, {
    global: { plugins: [i18n], stubs: { BrandLogo: true, BrandMark: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
  api.fetchPublicShare.mockReset().mockResolvedValue({ data: share() })
  api.getPublicDownloadUrl.mockReset()
  nav.href = ''
  vi.stubGlobal('location', nav)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('PublicShare downloads', () => {
  it('renders no download URL a scanner could follow', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.text()).toContain('Artikelstamm.txt')
    expect(w.html()).not.toMatch(/\/download(-zip)?\b/)
    expect(w.findAll('a[href]').filter((a) => a.attributes('href')?.includes('/api/'))).toEqual([])
    expect(api.getPublicDownloadUrl).not.toHaveBeenCalled()
  })

  it('a click asks for the URL and then opens it', async () => {
    api.getPublicDownloadUrl.mockResolvedValue({
      data: { url: '/api/public/tok-1/files/f1/download?t=1.sig' },
    })
    const w = mountView()
    await flushPromises()

    await w.find('.file-row button').trigger('click')
    await flushPromises()
    expect(api.getPublicDownloadUrl).toHaveBeenCalledWith('tok-1', 'f1')
    expect(nav.href).toBe('/api/public/tok-1/files/f1/download?t=1.sig')

    api.getPublicDownloadUrl.mockResolvedValue({
      data: { url: '/api/public/tok-1/download-zip?t=2.sig' },
    })
    await w.find('button.zip-all').trigger('click')
    await flushPromises()
    expect(api.getPublicDownloadUrl).toHaveBeenLastCalledWith('tok-1', null)
    expect(nav.href).toBe('/api/public/tok-1/download-zip?t=2.sig')
  })

  it('says on the page why a download was refused', async () => {
    api.getPublicDownloadUrl.mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 425,
        data: { error: 'Still scanning.', code: 'SCAN_IN_PROGRESS', details: {}, request_id: 'r' },
      },
    })
    const w = mountView()
    await flushPromises()

    await w.find('.file-row button').trigger('click')
    await flushPromises()
    expect(nav.href).toBe('')
    expect(w.find('[role="alert"]').text()).toContain('Still scanning.')
  })
})
