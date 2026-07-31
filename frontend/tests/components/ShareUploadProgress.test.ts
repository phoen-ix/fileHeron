import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import en from '@/i18n/locales/en.json'
import type { LogEntry, UploadItem } from '@/composables/useUpload'
import type { InlinePublicLinkResult } from '@/types/api'

import ShareUploadProgress from '@/components/ShareUploadProgress.vue'

function item(over: Partial<UploadItem> = {}): UploadItem {
  return {
    uid: 'u1',
    file: new File([new Uint8Array(10)], 'a.txt', { type: 'text/plain' }),
    state: 'uploading',
    progress: 40,
    fileId: null,
    error: null,
    errorCode: null,
    bytesUploaded: 4,
    ...over,
  }
}

function logEntry(over: Partial<LogEntry> = {}): LogEntry {
  return {
    id: 'l1',
    ts: 1_700_000_000_000,
    uid: 'u1',
    fileName: 'a.txt',
    kind: 'done',
    messageKey: 'share_create.progress.log.done',
    ...over,
  }
}

const PUBLIC_LINK: InlinePublicLinkResult = {
  id: 'pl1',
  url: 'https://example.com/d/tok',
  download_limit: null,
  downloads_remaining: null,
  notify_on_download: false,
  has_password: false,
  created_at: '2026-06-01T00:00:00Z',
} as InlinePublicLinkResult

function makeWrapper(props: Record<string, unknown>) {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(ShareUploadProgress, {
    global: { plugins: [i18n] },
    props: {
      items: [item()],
      publicLink: null,
      log: [],
      isActive: true,
      allDone: false,
      errorCount: 0,
      ...props,
    },
  })
}

describe('ShareUploadProgress', () => {
  it('shows the public-link box only when a link is present', () => {
    expect(makeWrapper({ publicLink: null }).find('.plaintext-box').exists()).toBe(false)
    const w = makeWrapper({ publicLink: PUBLIC_LINK })
    expect(w.find('.plaintext-box').exists()).toBe(true)
    expect(w.find('.plaintext-token').text()).toBe('https://example.com/d/tok')
  })

  it('hides action buttons while active, shows them when settled', () => {
    expect(makeWrapper({ isActive: true }).find('.actions').exists()).toBe(false)
    expect(makeWrapper({ isActive: false }).find('.actions').exists()).toBe(true)
  })

  it('emits view-share and create-another from the action buttons', async () => {
    const w = makeWrapper({ isActive: false, allDone: true })
    await w.find('.actions .fh-btn').trigger('click')
    await w.find('.actions .fh-btn-text').trigger('click')
    expect(w.emitted('view-share')).toBeTruthy()
    expect(w.emitted('create-another')).toBeTruthy()
  })

  it('re-emits retry for a failed file', async () => {
    const w = makeWrapper({
      items: [item({ state: 'error', error: 'boom' })],
      isActive: false,
      errorCount: 1,
    })
    // UploadFileRow renders a Retry button for error rows; clicking it
    // bubbles a retry event with the uid.
    const retryBtn = w
      .findAll('button')
      .find((b) => b.text() === en.upload.actions.retry)
    expect(retryBtn).toBeTruthy()
    await retryBtn!.trigger('click')
    expect(w.emitted('retry')).toEqual([['u1']])
  })

  it('renders translated, timestamped log lines', () => {
    const w = makeWrapper({
      log: [logEntry({ kind: 'done', messageKey: 'share_create.progress.log.done' })],
    })
    const entry = w.find('.log-entry')
    expect(entry.exists()).toBe(true)
    expect(entry.attributes('data-kind')).toBe('done')
    expect(entry.text()).toContain('a.txt')
    expect(entry.text()).toContain('done')
  })

  it('shows the partial-failure header when settled with errors', () => {
    const w = makeWrapper({ isActive: false, errorCount: 2 })
    expect(w.find('.progress-title').text()).toBe('2 file(s) failed')
  })
})
