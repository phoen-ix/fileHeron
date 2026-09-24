/* A finished file is already on the share; "Remove" on its row only dropped the
 * row, which read as undoing the upload while the file stayed attached. */
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import en from '@/i18n/locales/en.json'
import UploadFileRow from '@/components/UploadFileRow.vue'
import type { UploadItem } from '@/composables/useUpload'

function row(state: UploadItem['state']) {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  const item = { uid: 'u1', file: { name: 'a.bin', size: 10 }, state, progress: 0, bytesUploaded: 0, error: null, errorCode: null } as unknown as UploadItem
  return mount(UploadFileRow, { props: { item }, global: { plugins: [i18n] } })
}

const hasRemove = (w: ReturnType<typeof row>) =>
  w.findAll('button').some((b) => b.text() === en.upload.actions.remove)

describe('UploadFileRow', () => {
  it('offers Remove only before a file lands', () => {
    expect(hasRemove(row('queued'))).toBe(true)
    expect(hasRemove(row('error'))).toBe(true)
    expect(hasRemove(row('done'))).toBe(false)
  })
})
