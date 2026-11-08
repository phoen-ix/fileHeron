/* The step-up half of the SMTP and IMAP "Test" buttons.
 *
 * Testing a server other than the saved one, while relying on the stored mail
 * password, is the one case that could send that password somewhere new, so
 * the backend answers 403 STEP_UP_REQUIRED until the admin re-authenticates.
 * This owns the dialog state and the password: asked once the server refuses,
 * kept until a test PASSES (a failing server is a 200 `ok: false`, and the
 * admin will retry without retyping), cleared on a wrong password, which then
 * shows inside the dialog. The two settings pages carried this word for word,
 * with a doubled comment (audit 2026-11-08). */
import { ref } from 'vue'

import { asEnvelope } from '@/api/client'
import { useApiError } from '@/composables/useApiError'

export function useTestStepUp(runTest: () => Promise<void>) {
  const { describe } = useApiError()
  const confirmPassword = ref('')
  const stepUpOpen = ref(false)
  const stepUpError = ref<string | null>(null)

  /** The dialog's confirm: run the test again, this time with the password. */
  function onStepUpConfirm(password: string) {
    confirmPassword.value = password
    void runTest()
  }

  /** Spread into the test request body. */
  function confirmField(): { confirm_password?: string } {
    return confirmPassword.value ? { confirm_password: confirmPassword.value } : {}
  }

  /** After a test answered (200): forget the password only once it PASSED. */
  function onTestAnswered(ok: boolean) {
    if (ok) confirmPassword.value = ''
    stepUpOpen.value = false
  }

  /** True when the error was the step-up's to handle (open the dialog, or show
   *  a wrong password inside it); false leaves it to the page. */
  function handledTestError(err: unknown): boolean {
    if (asEnvelope(err)?.code === 'STEP_UP_REQUIRED') {
      confirmPassword.value = ''
      stepUpError.value = null
      stepUpOpen.value = true
      return true
    }
    if (stepUpOpen.value) {
      confirmPassword.value = ''
      stepUpError.value = describe(err)
      return true
    }
    return false
  }

  return {
    stepUpOpen,
    stepUpError,
    onStepUpConfirm,
    confirmField,
    onTestAnswered,
    handledTestError,
  }
}
