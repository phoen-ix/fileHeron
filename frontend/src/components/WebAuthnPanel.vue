<template>
  <section class="webauthn-panel">
    <h3 class="panel-h3">{{ t('webauthn.title') }}</h3>
    <p class="fh-field-help intro">{{ t('webauthn.intro') }}</p>

    <div
v-if="!supported" class="fh-notice" role="alert"
        data-tone="error">
      {{ t('webauthn.unsupported') }}
    </div>

    <div v-else>
      <ul v-if="credentials.length > 0" class="cred-list">
        <li v-for="c in credentials" :key="c.id" class="cred-row">
          <div class="cred-name">{{ c.name }}</div>
          <div class="cred-meta">
            <span class="fh-mono">
              {{ t('webauthn.created', { d: formatDate(c.created_at) }) }}
            </span>
            <span class="fh-mono">
              {{
                c.last_used_at
                  ? t('webauthn.last_used', { d: formatDate(c.last_used_at) })
                  : t('webauthn.never_used')
              }}
            </span>
          </div>
          <button
            type="button"
            class="fh-btn-text danger"
            :disabled="busy"
            @click="onDelete(c.id)"
          >
            {{ t('webauthn.remove') }}
          </button>
        </li>
      </ul>
      <p v-else class="fh-field-help empty">{{ t('webauthn.empty') }}</p>

      <div v-if="adding" class="add-form">
        <label class="fh-field">
          <span class="fh-field-label">{{ t('webauthn.name_label') }}</span>
          <input
            v-model.trim="newName"
            class="fh-field-input"
            type="text"
            maxlength="120"
            :placeholder="t('webauthn.name_placeholder')"
          />
        </label>
        <!-- Re-auth: a passkey that verifies its user counts as the second
             factor at login, so adding one is gated on the password, the way
             turning TOTP off is. -->
        <label class="fh-field">
          <span class="fh-field-label">{{ t('webauthn.password_label') }}</span>
          <input
            v-model="regPassword"
            class="fh-field-input"
            type="password"
            autocomplete="current-password"
          />
          <span class="fh-field-help">{{ t('webauthn.password_help') }}</span>
        </label>
        <div class="actions">
          <button
            type="button"
            class="fh-btn"
            :disabled="busy || !newName || !regPassword"
            @click="onRegister"
          >
            {{ busy ? t('common.loading') : t('webauthn.register') }}
          </button>
          <button type="button" class="fh-btn-text" @click="cancelAdd">
            {{ t('common.cancel') }}
          </button>
        </div>
      </div>
      <div v-else class="actions">
        <button type="button" class="fh-btn-ghost fh-btn" @click="adding = true">
          {{ t('webauthn.add_cta') }}
        </button>
      </div>

      <div
v-if="errorMsg" class="fh-notice" role="alert"
        data-tone="error">{{ errorMsg }}</div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  deleteCredential,
  listCredentials,
  registerBegin,
  registerComplete,
  type WebAuthnCredentialItem,
} from '@/api/webauthn'
import { useApiError } from '@/composables/useApiError'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { isWebAuthnSupported, performRegistration } from '@/composables/useWebAuthn'
import { useUiStore } from '@/stores/ui'

const { t } = useI18n()
const { formatDate } = useSiteDateFormat()
const { describe } = useApiError()
const ui = useUiStore()

const credentials = ref<WebAuthnCredentialItem[]>([])
const supported = ref(true)
const busy = ref(false)
const errorMsg = ref<string | null>(null)
const adding = ref(false)
const newName = ref('')
const regPassword = ref('')

function cancelAdd() {
  adding.value = false
  regPassword.value = ''
}

async function load() {
  try {
    const { data } = await listCredentials()
    credentials.value = data.items
  } catch {
    /* non-fatal - section may just stay empty */
  }
}

async function onRegister() {
  errorMsg.value = null
  busy.value = true
  try {
    const { data } = await registerBegin(regPassword.value)
    // The server returns the options under {options: ...}; py_webauthn's
    // options_to_json shape uses the standard keys (challenge, rp, user, …).
    const opts = data.options as Record<string, unknown>
    // py_webauthn options_to_json renames to camelCase already.
    const cred = await performRegistration({
      challenge: opts.challenge as string,
      rp: opts.rp as { id: string; name: string },
      user: opts.user as { id: string; name: string; displayName: string },
      pubKeyCredParams: (opts.pubKeyCredParams || opts.pub_key_cred_params) as {
        alg: number
        type: 'public-key'
      }[],
      excludeCredentials: (opts.excludeCredentials ||
        opts.exclude_credentials) as
        | { id: string; type: 'public-key'; transports?: string[] }[]
        | undefined,
      authenticatorSelection:
        (opts.authenticatorSelection || opts.authenticator_selection) as
          | {
              userVerification?: 'required' | 'preferred' | 'discouraged'
              residentKey?: 'required' | 'preferred' | 'discouraged'
            }
          | undefined,
      attestation: opts.attestation as 'none' | 'indirect' | 'direct' | undefined,
    })
    await registerComplete(newName.value, cred)
    adding.value = false
    newName.value = ''
    regPassword.value = ''
    ui.pushToast(t('webauthn.added_toast'), 'success')
    await load()
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'NotAllowedError') {
      errorMsg.value = t('webauthn.cancelled')
    } else {
      errorMsg.value = describe(err) || (err as Error).message
    }
  } finally {
    busy.value = false
  }
}

async function onDelete(id: number) {
  if (!(await ui.confirm({ message: t('webauthn.remove_confirm'), danger: true }))) return
  busy.value = true
  try {
    await deleteCredential(id)
    credentials.value = credentials.value.filter((c) => c.id !== id)
    ui.pushToast(t('webauthn.removed_toast'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    busy.value = false
  }
}


onMounted(() => {
  supported.value = isWebAuthnSupported()
  if (supported.value) void load()
})
</script>

<style scoped>
.webauthn-panel {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.panel-h3 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
  margin: 0 0 var(--fh-space-2);
}

.intro {
  margin: 0 0 var(--fh-space-3);
  max-width: 60ch;
}

.cred-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: var(--fh-border);
}

.cred-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) auto;
  gap: var(--fh-space-3);
  align-items: center;
  padding: var(--fh-space-3) 0;
  border-bottom: var(--fh-border);
}

.cred-name {
  color: var(--fh-ink);
}

.cred-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-3);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.empty {
  margin: var(--fh-space-3) 0;
}

.add-form {
  background: var(--fh-paper-raised);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  padding: var(--fh-space-3);
  margin: var(--fh-space-3) 0;
}

.actions {
  display: flex;
  gap: var(--fh-space-3);
  margin-top: var(--fh-space-3);
}

.fh-btn-text.danger {
  color: var(--fh-danger);
}

@media (max-width: 720px) {
  .cred-row {
    grid-template-columns: 1fr;
  }
}
</style>
