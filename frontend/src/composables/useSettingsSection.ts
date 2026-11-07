/* The load → edit → save skeleton every admin settings section repeats.
 *
 * Twenty-five settings views and section components each hand-rolled the same
 * `loading` / `saving` / `errorMsg` trio, the same try/catch around the GET
 * and the PUT, the same "describe the API error" and the same success toast -
 * the five `components/admin/*Section.vue` were 44-line clones of each other
 * (audit 2026-11-07). The form itself stays in the component: this owns only
 * the lifecycle around it. */
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'

export interface SettingsSectionOptions<T> {
  /** GET the current values. */
  load: () => Promise<{ data: T }>
  /** PUT the form; the server answers with the values it stored. */
  save: () => Promise<{ data: T }>
  /** Copy a response into the form refs - after the load AND after the save,
   *  so the form shows what the server kept, not what was typed. */
  apply: (data: T) => void
  /** i18n key of the success toast. */
  savedToastKey: string
  /** Runs after a successful save, before the toast - e.g. `auth.refreshMe()`
   *  for a setting the signed-in user's own `/me` reflects. */
  afterSave?: () => Promise<unknown> | void
  /** Load on mount (default). Pass `false` when the parent drives the load. */
  immediate?: boolean
}

export function useSettingsSection<T>(opts: SettingsSectionOptions<T>) {
  const { t } = useI18n()
  const { describe } = useApiError()
  const ui = useUiStore()

  const loading = ref(true)
  const saving = ref(false)
  const errorMsg = ref<string | null>(null)

  async function load() {
    loading.value = true
    errorMsg.value = null
    try {
      const { data } = await opts.load()
      opts.apply(data)
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      loading.value = false
    }
  }

  async function onSave() {
    saving.value = true
    errorMsg.value = null
    try {
      const { data } = await opts.save()
      opts.apply(data)
      await opts.afterSave?.()
      ui.pushToast(t(opts.savedToastKey), 'success')
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      saving.value = false
    }
  }

  if (opts.immediate !== false) onMounted(load)

  return { loading, saving, errorMsg, load, onSave }
}
