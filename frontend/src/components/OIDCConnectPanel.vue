<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import {
  disconnectOIDC,
  getOIDCLink,
  startConnect,
  type OIDCLinkItem,
} from '@/api/oidcConnect'
import { getPublicConfig, type PublicProvider } from '@/api/oidc'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()
const route = useRoute()
const router = useRouter()

const providers = ref<PublicProvider[]>([])
const link = ref<OIDCLinkItem | null>(null)
const loading = ref(true)
const errorMsg = ref<string | null>(null)
const connectingId = ref<string | null>(null)
const disconnecting = ref(false)

const linkedProviderId = computed(() => link.value?.provider_id ?? null)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const [{ data: cfg }, { data: linkResp }] = await Promise.all([
      getPublicConfig(),
      getOIDCLink(),
    ])
    providers.value = cfg.providers
    link.value = linkResp.link
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function onConnect(p: PublicProvider) {
  connectingId.value = p.id
  errorMsg.value = null
  try {
    const { data } = await startConnect(p.id)
    window.location.href = data.redirect_url
  } catch (err) {
    errorMsg.value = describe(err)
    connectingId.value = null
  }
}

async function onDisconnect() {
  if (!window.confirm(t('account_oidc.disconnect_confirm'))) return
  disconnecting.value = true
  errorMsg.value = null
  try {
    await disconnectOIDC()
    ui.pushToast(t('account_oidc.disconnected_toast'), 'success')
    await load()
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    disconnecting.value = false
  }
}

onMounted(async () => {
  await load()
  // After a successful connect round-trip, the IdP bounces us back
  // to /account?oidc_connected=1. Surface a toast and clean the URL.
  if (route.query.oidc_connected === '1') {
    ui.pushToast(t('account_oidc.connected_toast'), 'success')
    void router.replace({ path: route.path, query: {} })
  }
})
</script>

<template>
  <section class="account-section">
    <h2 class="account-h2">{{ t('account_oidc.section_title') }}</h2>
    <p class="fh-field-help" style="margin-bottom: var(--fh-space-3)">
      {{ t('account_oidc.section_help') }}
    </p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <div
      v-else-if="errorMsg"
      class="fh-notice"
      data-tone="error"
    >
      {{ errorMsg }}
    </div>

    <div
      v-else-if="providers.length === 0"
      class="fh-notice"
      data-tone="muted"
    >
      {{ t('account_oidc.no_providers') }}
    </div>

    <ul v-else class="oidc-list">
      <li v-for="p in providers" :key="p.id" class="oidc-row">
        <div class="oidc-info">
          <strong class="oidc-name">{{ p.name }}</strong>
          <code class="fh-mono oidc-preset">{{ p.preset }}</code>
        </div>
        <div class="oidc-actions">
          <template v-if="linkedProviderId === p.id">
            <span class="fh-pill" data-state="active">
              {{ t('account_oidc.linked_label', { hint: link?.sub_hint }) }}
            </span>
            <button
              type="button"
              class="fh-btn fh-btn-ghost"
              :disabled="disconnecting"
              @click="onDisconnect"
            >
              {{ disconnecting ? t('common.loading') : t('account_oidc.disconnect') }}
            </button>
          </template>
          <template v-else-if="linkedProviderId">
            <span class="fh-pill" data-state="muted">
              {{ t('account_oidc.other_linked') }}
            </span>
          </template>
          <template v-else>
            <button
              type="button"
              class="fh-btn"
              :disabled="connectingId === p.id"
              @click="onConnect(p)"
            >
              {{ connectingId === p.id ? t('common.loading') : t('account_oidc.connect') }}
            </button>
          </template>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-3) 0;
}

.oidc-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.oidc-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fh-space-3);
  padding: var(--fh-space-3);
  border: 1px solid var(--fh-rule);
  border-radius: var(--fh-radius-sm, 4px);
}

.oidc-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.oidc-name {
  font-weight: 500;
}

.oidc-preset {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.oidc-actions {
  display: flex;
  gap: var(--fh-space-2);
  align-items: center;
}

.account-section {
  margin-top: var(--fh-space-5);
}

.account-h2 {
  font-family: var(--fh-font-display);
  font-size: var(--fh-text-h2);
  margin-bottom: var(--fh-space-2);
}
</style>
