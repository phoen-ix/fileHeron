<!-- The Legal pages tab of Branding & legal: the imprint and privacy pages,
     each switchable and written per language. It was the bottom half of the
     branding form, with its own Save; the two halves share nothing but the
     menu item. -->
<script setup lang="ts">
  import { onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { getLegalSettings, updateLegalSettings, type LegalSettingsResponse } from '@/api/admin'
  import RichTextEditor from '@/components/RichTextEditor.vue'
  import { useApiError } from '@/composables/useApiError'
  import { SUPPORTED_LOCALES, type SupportedLocale } from '@/i18n'
  import { useSiteStore } from '@/stores/site'
  import { useUiStore } from '@/stores/ui'

  const { t, te } = useI18n()
  const { describe } = useApiError()
  const ui = useUiStore()
  const site = useSiteStore()

  const loading = ref(true)
  const errorMsg = ref<string | null>(null)
  const legal = ref<LegalSettingsResponse | null>(null)
  const saving = ref(false)

  // Which language the editors show (one at a time, so they do not cramp as
  // languages are added). An in-page switch, not tabs: AdminTabs owns the
  // tablist on this page, and these buttons have no panel of their own.
  const activeLocale = ref<SupportedLocale>('en')

  function langLabel(code: SupportedLocale): string {
    const k = `admin_branding.legal.lang_${code}`
    return te(k) ? t(k) : code.toUpperCase()
  }

  async function load() {
    loading.value = true
    errorMsg.value = null
    try {
      legal.value = (await getLegalSettings()).data
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      loading.value = false
    }
  }

  async function save() {
    if (!legal.value) return
    saving.value = true
    try {
      const { data } = await updateLegalSettings(legal.value)
      legal.value = data
      await site.loadConfig()
      ui.pushToast(t('common.saved'), 'success')
    } catch (err) {
      ui.pushToast(describe(err), 'error')
    } finally {
      saving.value = false
    }
  }

  onMounted(load)
</script>

<template>
  <div class="legal-page" data-density="operator">
    <p class="fh-field-help intro">{{ t('admin_branding.legal.help') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

    <template v-else-if="legal">
      <div
        class="locale-switch"
        role="group"
        :aria-label="t('admin_branding.legal.language_tab_group')"
      >
        <button
          v-for="loc in SUPPORTED_LOCALES"
          :key="loc"
          type="button"
          class="locale-option"
          :class="{ active: loc === activeLocale }"
          :aria-pressed="loc === activeLocale"
          @click="activeLocale = loc"
        >
          {{ langLabel(loc) }}
        </button>
      </div>

      <div v-for="kind in ['imprint', 'privacy'] as const" :key="kind" class="legal-doc">
        <label class="toggle-row">
          <input v-model="legal[kind].enabled" type="checkbox" />
          <span>{{ t(`admin_branding.legal.${kind}_enable`) }}</span>
        </label>
        <!-- Every language's editor stays mounted (v-show, not v-if) so a fast
             switch never drops the last debounced keystroke. -->
        <div
          v-for="loc in SUPPORTED_LOCALES"
          v-show="loc === activeLocale"
          :key="loc"
          class="legal-lang"
        >
          <RichTextEditor
            v-model="legal[kind][loc]"
            :aria-label="t(`admin_branding.legal.${kind}_enable`) + ' ' + loc.toUpperCase()"
          />
        </div>
      </div>

      <div class="actions">
        <button type="button" class="fh-btn" :disabled="saving" @click="save">
          {{ saving ? t('common.loading') : t('common.save') }}
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
  .legal-page {
    max-width: none;
  }
  .intro {
    max-width: 64ch;
    margin: 0 0 var(--fh-space-3);
  }
  .loading {
    color: var(--fh-subtle);
    padding: var(--fh-space-3) 0;
  }
  .toggle-row {
    display: flex;
    align-items: center;
    gap: var(--fh-space-2);
  }
  .legal-doc {
    margin: var(--fh-space-3) 0 var(--fh-space-4);
  }
  .locale-switch {
    display: flex;
    gap: var(--fh-space-1);
    margin: var(--fh-space-2) 0 var(--fh-space-3);
    border-bottom: 1px solid var(--fh-hairline);
  }
  .locale-option {
    padding: 0.4rem 0.9rem;
    border: none;
    border-bottom: 2px solid transparent;
    background: none;
    color: var(--fh-ink-soft);
    font-family: var(--fh-font-body);
    cursor: pointer;
  }
  .locale-option.active {
    color: var(--fh-ink);
    border-bottom-color: var(--fh-accent);
  }
  .legal-lang {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-1);
    margin-top: var(--fh-space-2);
  }
  .actions {
    margin-top: var(--fh-space-3);
  }
</style>
