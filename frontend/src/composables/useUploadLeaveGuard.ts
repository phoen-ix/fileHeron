import { onBeforeUnmount, onMounted, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteLeave } from 'vue-router'

import { useUiStore } from '@/stores/ui'

/**
 * Ask before leaving a page whose uploads are still running.
 *
 * `useUpload` cancels everything when its component unmounts, and for a
 * resumable upload `@uppy/tus` turns cancel into a tus termination - a DELETE of
 * the upload on the server. So clicking "Inbox" (or pressing `g i`, or reloading)
 * halfway through a multi-GB transfer threw the bytes away without a word.
 * In-app navigation gets the app's own confirm dialog; a reload or tab close
 * gets the browser's native one (the only thing `beforeunload` may show).
 */
export function useUploadLeaveGuard(active: Ref<boolean>) {
  const { t } = useI18n()
  const ui = useUiStore()

  function onBeforeUnload(e: BeforeUnloadEvent) {
    if (!active.value) return
    e.preventDefault()
    e.returnValue = ''
  }

  onMounted(() => window.addEventListener('beforeunload', onBeforeUnload))
  onBeforeUnmount(() => window.removeEventListener('beforeunload', onBeforeUnload))

  onBeforeRouteLeave(() => {
    if (!active.value) return true
    return ui.confirm({
      title: t('upload.leave.title'),
      message: t('upload.leave.message'),
      confirmLabel: t('upload.leave.confirm'),
      danger: true,
    })
  })
}
