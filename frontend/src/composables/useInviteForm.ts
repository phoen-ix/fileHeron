import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { inviteUser } from '@/api/account'
import { listGroups } from '@/api/groups'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'
import type { GroupResponse, UserRole } from '@/types/api'

/**
 * State machine for the "Invite a user" modal in the admin users view.
 * Owns the form fields, group-checkbox state, submit lifecycle, and the
 * toast-on-success side effect. The view binds inputs to the returned
 * refs and calls `openInviteForm()` / `closeInviteForm()` / `onInvite()`.
 */
export function useInviteForm() {
  const { t, locale } = useI18n()
  const { describe } = useApiError()
  const ui = useUiStore()

  const showInviteForm = ref(false)
  const inviteEmail = ref('')
  const inviteDisplayName = ref('')
  const inviteRole = ref<UserRole>('client')
  const inviting = ref(false)
  const inviteError = ref<string | null>(null)
  const availableGroups = ref<GroupResponse[]>([])
  const selectedGroupIds = ref<number[]>([])

  function resetInviteForm() {
    inviteEmail.value = ''
    inviteDisplayName.value = ''
    inviteRole.value = 'client'
    selectedGroupIds.value = []
    inviteError.value = null
  }

  function closeInviteForm() {
    showInviteForm.value = false
    resetInviteForm()
  }

  async function openInviteForm() {
    showInviteForm.value = true
    // Lazy-load groups the first time the form is opened.
    if (availableGroups.value.length === 0) {
      try {
        const { data } = await listGroups()
        availableGroups.value = data.items
      } catch {
        /* leave empty — checkbox section just won't render */
      }
    }
  }

  function toggleGroup(id: number) {
    const idx = selectedGroupIds.value.indexOf(id)
    if (idx === -1) {
      selectedGroupIds.value = [...selectedGroupIds.value, id]
    } else {
      selectedGroupIds.value = selectedGroupIds.value.filter((g) => g !== id)
    }
  }

  function _formatDate(iso: string | null): string {
    if (!iso) return '—'
    return new Date(iso).toLocaleDateString(
      locale.value === 'de' ? 'de-AT' : 'en-US',
      { year: 'numeric', month: 'short', day: '2-digit' },
    )
  }

  async function onInvite() {
    inviting.value = true
    inviteError.value = null
    try {
      const { data } = await inviteUser({
        email: inviteEmail.value,
        display_name_hint: inviteDisplayName.value,
        target_role: inviteRole.value,
        initial_group_ids: selectedGroupIds.value,
      })
      ui.pushToast(
        t('admin_users.invite_sent', {
          hint: data.email,
          expires: _formatDate(data.expires_at),
        }),
        'success',
      )
      closeInviteForm()
    } catch (err) {
      inviteError.value = describe(err)
    } finally {
      inviting.value = false
    }
  }

  return {
    // state
    showInviteForm,
    inviteEmail,
    inviteDisplayName,
    inviteRole,
    inviting,
    inviteError,
    availableGroups,
    selectedGroupIds,
    // methods
    openInviteForm,
    closeInviteForm,
    toggleGroup,
    onInvite,
  }
}
