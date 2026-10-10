/* Folder uploads (v2.26.0): the drop zone takes a folder by drop (through
 * Uppy's walker, which opens what `dataTransfer.files` cannot) and by a
 * folder picker, and hands the files on with their paths intact. */
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'

const walked: File[] = []
vi.mock('@uppy/core/utils', () => ({
  getDroppedFiles: vi.fn(async () => walked),
}))

import { getDroppedFiles } from '@uppy/core/utils'

import en from '@/i18n/locales/en.json'
import FileUploadArea from '@/components/FileUploadArea.vue'

function mountArea() {
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
  return mount(FileUploadArea, { props: { items: [] }, global: { plugins: [i18n] } })
}

describe('FileUploadArea', () => {
  it('a drop goes through the folder walker and emits what it found', async () => {
    const inFolder = new File(['x'], 'a.jpg')
    Object.defineProperty(inFolder, 'relativePath', { value: 'Photos/a.jpg' })
    walked.splice(0, walked.length, inFolder)
    const w = mountArea()
    const dataTransfer = { items: [], files: [] }
    await w.find('label.dropzone').trigger('drop', { dataTransfer })
    await vi.waitFor(() => expect(w.emitted('add')).toBeTruthy())
    expect(vi.mocked(getDroppedFiles)).toHaveBeenCalledWith(dataTransfer)
    expect(w.emitted('add')![0][0]).toEqual([inFolder])
  })

  it('has a folder picker, and the button opens it rather than the file picker', async () => {
    const w = mountArea()
    const folder = w.find('input.folder-input')
    expect(folder.attributes()).toHaveProperty('webkitdirectory')
    const files = w.find('input.dropzone-input')
    expect(files.attributes()).not.toHaveProperty('webkitdirectory')
    const clicked = vi.spyOn(folder.element as HTMLInputElement, 'click')
    await w.find('.folder-pick button').trigger('click')
    expect(clicked).toHaveBeenCalledTimes(1)
  })
})
