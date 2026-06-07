<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  deleteBrandingLogo,
  getBrandingSettings,
  getLegalSettings,
  updateBrandingSettings,
  updateLegalSettings,
  uploadBrandingLogo,
  type BrandingSettingsResponse,
  type LegalSettingsResponse,
} from '@/api/admin'
import RichTextEditor from '@/components/RichTextEditor.vue'
import { useApiError } from '@/composables/useApiError'
import { SUPPORTED_LOCALES, type SupportedLocale } from '@/i18n'
import { useSiteStore } from '@/stores/site'
import { useUiStore } from '@/stores/ui'

const { t, te } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()
const site = useSiteStore()

const ACCEPT = ['image/png', 'image/jpeg', 'image/webp']
const MAX_BYTES = 2 * 1024 * 1024

const loading = ref(true)
const errorMsg = ref<string | null>(null)

const branding = ref<BrandingSettingsResponse | null>(null)
const legal = ref<LegalSettingsResponse | null>(null)

// Which language the legal editor currently shows (one at a time, via tabs).
const activeLegalLocale = ref<SupportedLocale>('en')

function legalLangLabel(code: SupportedLocale): string {
  const k = `admin_branding.legal.lang_${code}`
  return te(k) ? t(k) : code.toUpperCase()
}

const savingBranding = ref(false)
const savingLegal = ref(false)
const uploading = ref(false)
const cacheBust = ref(0)

const fileInput = ref<HTMLInputElement | null>(null)

const logoSrc = computed(() =>
  branding.value?.logo.present ? `/api/branding/logo?v=${cacheBust.value}` : null,
)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const [{ data: b }, { data: l }] = await Promise.all([
      getBrandingSettings(),
      getLegalSettings(),
    ])
    branding.value = b
    legal.value = l
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function onPickFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (!ACCEPT.includes(file.type)) {
    ui.pushToast(t('admin_branding.logo.invalid_type'), 'error')
    input.value = ''
    return
  }
  if (file.size > MAX_BYTES) {
    ui.pushToast(t('admin_branding.logo.too_large'), 'error')
    input.value = ''
    return
  }
  uploading.value = true
  try {
    const { data } = await uploadBrandingLogo(file)
    branding.value = data
    cacheBust.value = Date.now()
    await site.loadConfig()
    ui.pushToast(t('admin_branding.logo.uploaded'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    uploading.value = false
    input.value = ''
  }
}

async function onDeleteLogo() {
  uploading.value = true
  try {
    const { data } = await deleteBrandingLogo()
    branding.value = data
    cacheBust.value = Date.now()
    await site.loadConfig()
    ui.pushToast(t('admin_branding.logo.deleted'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    uploading.value = false
  }
}

async function saveBranding() {
  if (!branding.value) return
  savingBranding.value = true
  try {
    const b = branding.value
    const { data } = await updateBrandingSettings({
      show_header: b.show_header,
      show_login: b.show_login,
      show_public: b.show_public,
      show_email: b.show_email,
      show_client: b.show_client,
      link_url: b.link_url ?? '',
    })
    branding.value = data
    await site.loadConfig()
    ui.pushToast(t('common.saved'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    savingBranding.value = false
  }
}

async function saveLegal() {
  if (!legal.value) return
  savingLegal.value = true
  try {
    const { data } = await updateLegalSettings(legal.value)
    legal.value = data
    await site.loadConfig()
    ui.pushToast(t('common.saved'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    savingLegal.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="branding-page" data-density="operator">
    <span class="fh-eyebrow">
      {{ t('admin_settings.eyebrow') }} / {{ t('admin_branding.title') }}
    </span>
    <p class="fh-field-help intro">{{ t('admin_branding.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

    <template v-else>
      <!-- Logo ------------------------------------------------------------ -->
      <section class="settings-section">
        <h2 class="settings-h2">{{ t('admin_branding.logo.title') }}</h2>
        <p class="fh-field-help">{{ t('admin_branding.logo.help') }}</p>

        <div class="logo-row">
          <div class="logo-preview">
            <img v-if="logoSrc" :src="logoSrc" alt="" />
            <span v-else class="logo-empty">{{ t('admin_branding.logo.none') }}</span>
          </div>
          <div class="logo-actions">
            <input
              ref="fileInput"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              class="visually-hidden"
              @change="onPickFile"
            />
            <button type="button" class="fh-btn" :disabled="uploading" @click="fileInput?.click()">
              {{ uploading ? t('common.loading') : t('admin_branding.logo.upload') }}
            </button>
            <button
              v-if="branding?.logo.present"
              type="button"
              class="fh-btn-text danger"
              :disabled="uploading"
              @click="onDeleteLogo"
            >
              {{ t('admin_branding.logo.remove') }}
            </button>
          </div>
        </div>

        <fieldset class="surfaces">
          <legend class="fh-field-label">{{ t('admin_branding.surfaces.title') }}</legend>
          <p class="fh-field-help">{{ t('admin_branding.surfaces.help') }}</p>
          <label class="check"><input v-model="branding!.show_header" type="checkbox" /><span>{{ t('admin_branding.surfaces.header') }}</span></label>
          <label class="check"><input v-model="branding!.show_login" type="checkbox" /><span>{{ t('admin_branding.surfaces.login') }}</span></label>
          <label class="check"><input v-model="branding!.show_public" type="checkbox" /><span>{{ t('admin_branding.surfaces.public') }}</span></label>
          <label class="check"><input v-model="branding!.show_email" type="checkbox" /><span>{{ t('admin_branding.surfaces.email') }}</span></label>
          <label class="check"><input v-model="branding!.show_client" type="checkbox" /><span>{{ t('admin_branding.surfaces.client') }}</span></label>
        </fieldset>

        <label class="fh-field">
          <span class="fh-field-label">{{ t('admin_branding.link.label') }}</span>
          <input
            v-model="branding!.link_url"
            class="fh-field-input fh-mono"
            type="url"
            placeholder="https://example.com"
          />
          <span class="fh-field-help">{{ t('admin_branding.link.help') }}</span>
        </label>

        <div class="actions">
          <button type="button" class="fh-btn" :disabled="savingBranding" @click="saveBranding">
            {{ savingBranding ? t('common.loading') : t('common.save') }}
          </button>
        </div>
      </section>

      <hr class="fh-rule" />

      <!-- Legal ----------------------------------------------------------- -->
      <section class="settings-section">
        <h2 class="settings-h2">{{ t('admin_branding.legal.title') }}</h2>
        <p class="fh-field-help">{{ t('admin_branding.legal.help') }}</p>

        <!-- Language tab: show one language at a time so the editor doesn't cramp
             as more languages are added (scales by SUPPORTED_LOCALES). -->
        <div class="locale-tabs" role="tablist" :aria-label="t('admin_branding.legal.language_tab_group')">
          <button
            v-for="loc in SUPPORTED_LOCALES"
            :key="loc"
            type="button"
            role="tab"
            class="locale-tab"
            :class="{ active: loc === activeLegalLocale }"
            :aria-selected="loc === activeLegalLocale"
            @click="activeLegalLocale = loc"
          >
            {{ legalLangLabel(loc) }}
          </button>
        </div>

        <template v-for="kind in (['imprint', 'privacy'] as const)" :key="kind">
          <div class="legal-doc">
            <label class="toggle-row">
              <input v-model="legal![kind].enabled" type="checkbox" />
              <span class="toggle-name">{{ t(`admin_branding.legal.${kind}_enable`) }}</span>
            </label>
            <!-- Every locale's editor stays mounted (v-show, not v-if) so a fast
                 tab switch never drops the last debounced keystroke. -->
            <div
              v-for="loc in SUPPORTED_LOCALES"
              v-show="loc === activeLegalLocale"
              :key="loc"
              class="legal-lang"
            >
              <RichTextEditor
                v-model="legal![kind][loc]"
                :aria-label="t(`admin_branding.legal.${kind}_enable`) + ' ' + loc.toUpperCase()"
              />
            </div>
          </div>
        </template>

        <div class="actions">
          <button type="button" class="fh-btn" :disabled="savingLegal" @click="saveLegal">
            {{ savingLegal ? t('common.loading') : t('common.save') }}
          </button>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.branding-page {
  max-width: 860px;
}
.intro {
  max-width: 64ch;
  margin: 0 0 var(--fh-space-3);
}
.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-3) 0;
}
.settings-section {
  margin: var(--fh-space-4) 0;
}
.settings-h2 {
  font-family: var(--fh-font-display);
  font-weight: 400;
  font-size: 1.4rem;
  margin: 0 0 var(--fh-space-2);
}
.logo-row {
  display: flex;
  align-items: center;
  gap: var(--fh-space-4);
  margin: var(--fh-space-2) 0;
}
.logo-preview {
  min-width: 160px;
  min-height: 64px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--fh-space-2);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  background: var(--fh-paper-raised);
}
.logo-preview img {
  max-height: 64px;
  max-width: 240px;
  width: auto;
}
.logo-empty {
  color: var(--fh-subtle);
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
}
.logo-actions {
  display: flex;
  align-items: center;
  gap: var(--fh-space-3);
}
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
}
.surfaces {
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-3);
  margin: var(--fh-space-3) 0;
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}
.check,
.toggle-row {
  display: flex;
  align-items: center;
  gap: var(--fh-space-2);
}
.legal-doc {
  margin: var(--fh-space-3) 0 var(--fh-space-4);
}
.locale-tabs {
  display: flex;
  gap: var(--fh-space-1);
  margin: var(--fh-space-2) 0 var(--fh-space-3);
  border-bottom: 1px solid var(--fh-hairline);
}
.locale-tab {
  padding: 0.4rem 0.9rem;
  border: none;
  border-bottom: 2px solid transparent;
  background: none;
  color: var(--fh-ink-soft);
  font-family: var(--fh-font-body);
  cursor: pointer;
}
.locale-tab.active {
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
.fh-btn-text.danger {
  color: var(--fh-danger);
}
</style>
