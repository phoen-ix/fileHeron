<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import {
    deleteBrandingLogo,
    getBrandingSettings,
    updateBrandingSettings,
    uploadBrandingLogo,
    type BrandingSettingsResponse,
  } from '@/api/admin'
  import TunableFields from '@/components/admin/TunableFields.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useSiteStore } from '@/stores/site'
  import { useUiStore } from '@/stores/ui'

  const { t } = useI18n()
  const { describe } = useApiError()
  const ui = useUiStore()
  const site = useSiteStore()

  const ACCEPT = ['image/png', 'image/jpeg', 'image/webp']
  const MAX_BYTES = 2 * 1024 * 1024

  const loading = ref(true)
  const errorMsg = ref<string | null>(null)

  const branding = ref<BrandingSettingsResponse | null>(null)

  const savingBranding = ref(false)
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
      branding.value = (await getBrandingSettings()).data
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

  onMounted(load)
</script>

<template>
  <div class="branding-page" data-density="operator">
    <p class="fh-field-help intro">{{ t('admin_branding.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

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
              :aria-label="t('common.filter')"
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
          <label class="check"
            ><input v-model="branding!.show_header" type="checkbox" /><span>{{
              t('admin_branding.surfaces.header')
            }}</span></label
          >
          <label class="check"
            ><input v-model="branding!.show_login" type="checkbox" /><span>{{
              t('admin_branding.surfaces.login')
            }}</span></label
          >
          <label class="check"
            ><input v-model="branding!.show_public" type="checkbox" /><span>{{
              t('admin_branding.surfaces.public')
            }}</span></label
          >
          <label class="check"
            ><input v-model="branding!.show_email" type="checkbox" /><span>{{
              t('admin_branding.surfaces.email')
            }}</span></label
          >
          <label class="check"
            ><input v-model="branding!.show_client" type="checkbox" /><span>{{
              t('admin_branding.surfaces.client')
            }}</span></label
          >
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

      <section class="settings-section">
        <h2 class="settings-h2">{{ t('admin_branding.name_title') }}</h2>
        <TunableFields route="admin-settings-branding" :headings="false" />
      </section>
    </template>
  </div>
</template>

<style scoped>
  .branding-page {
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
  .check {
    display: flex;
    align-items: center;
    gap: var(--fh-space-2);
  }
  .actions {
    margin-top: var(--fh-space-3);
  }
  .fh-btn-text.danger {
    color: var(--fh-danger);
  }
</style>
