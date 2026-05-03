<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import AuthCanvas from '@/components/AuthCanvas.vue'
import { asEnvelope } from '@/api/client'
import { getPublicConfig, oidcStartUrl, type PublicProvider } from '@/api/oidc'
import { useApiError } from '@/composables/useApiError'
import { effectiveLandingPath } from '@/composables/useEffectiveLanding'
import { isWebAuthnSupported } from '@/composables/useWebAuthn'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const { describe } = useApiError()
const { t } = useI18n()

type Mode = 'creds' | 'totp' | 'recovery'

const email = ref('')
const password = ref('')
const totpCode = ref('')
const recoveryCode = ref('')
const mode = ref<Mode>('creds')
const error = ref<string | null>(null)
const submitting = ref(false)

const totpInputRef = ref<HTMLInputElement | null>(null)
const recoveryInputRef = ref<HTMLInputElement | null>(null)

// Show the "Use passkey" button only when the browser supports
// WebAuthn AND the user is past the password step. The browser
// support check is computed once on script setup; navigator API
// availability doesn't change at runtime.
const passkeySupported = isWebAuthnSupported()

const providers = ref<PublicProvider[]>([])
;(async () => {
  try {
    const { data } = await getPublicConfig()
    providers.value = data.providers
  } catch {
    /* config endpoint can fail in dev — keep buttons hidden */
  }
})()

function onProviderClick(p: PublicProvider) {
  window.location.href = oidcStartUrl(p.id)
}

watch(mode, async (m) => {
  await nextTick()
  if (m === 'totp') totpInputRef.value?.focus()
  else if (m === 'recovery') recoveryInputRef.value?.focus()
})

const submitLabel = computed(() => (submitting.value ? 'login.submitting' : 'login.submit'))
// Honour an explicit ?redirect=... ("back to where I was going");
// otherwise compute the user's effective landing once they've logged
// in. Login flow: auth.login() populates auth.user, so by the time we
// evaluate `redirectTo` the MeResponse is in the store.
const redirectTo = computed(() => {
  const explicit = route.query.redirect as string | undefined
  if (explicit) return explicit
  return effectiveLandingPath(auth.user)
})

async function onSubmit() {
  error.value = null
  submitting.value = true
  try {
    if (mode.value === 'recovery') {
      await auth.loginWithRecovery(email.value, password.value, recoveryCode.value)
    } else {
      await auth.login(email.value, password.value, totpCode.value || undefined)
    }
    await router.push(redirectTo.value)
  } catch (e) {
    const env = asEnvelope(e)
    if (env?.code === 'TOTP_REQUIRED') {
      mode.value = 'totp'
      error.value = null
    } else {
      error.value = describe(e)
    }
  } finally {
    submitting.value = false
  }
}

// Passkey-as-second-factor login. Triggered from the TOTP step when
// the user has registered a passkey on this account. The backend
// returns a WebAuthn challenge after validating the password; the
// browser prompts the user, signs the challenge, and we ship the
// assertion back to mint the same JWT + refresh cookie as the
// password+TOTP path.
async function tryPasskey() {
  error.value = null
  submitting.value = true
  try {
    await auth.loginWithPasskey(email.value, password.value)
    await router.push(redirectTo.value)
  } catch (e) {
    if (e instanceof DOMException && e.name === 'NotAllowedError') {
      // User cancelled the platform prompt or it timed out.
      error.value = t('login.passkey_cancelled')
    } else {
      error.value = describe(e)
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <AuthCanvas>
    <span class="fh-eyebrow fh-rise" data-stagger="1">{{ $t('login.title') }}</span>
    <h1 class="fh-display fh-rise" data-stagger="2">{{ $t('login.subtitle') }}</h1>

    <div
      v-if="providers.length > 0 && mode === 'creds'"
      class="oidc-btns fh-rise"
      data-stagger="3"
    >
      <button
        v-for="p in providers"
        :key="p.id"
        type="button"
        class="fh-btn fh-btn-ghost oidc-btn"
        :data-preset="p.preset"
        @click="onProviderClick(p)"
      >
        {{ $t('login.sso_button_with_name', { name: p.name }) }}
        <span aria-hidden="true">→</span>
      </button>
    </div>
    <div
      v-if="providers.length > 0 && mode === 'creds'"
      class="fh-rule oidc-rule fh-rise"
      data-stagger="3"
      role="separator"
      :data-label="$t('login.sso_or')"
    ></div>

    <form class="form fh-rise" data-stagger="3" novalidate @submit.prevent="onSubmit">
      <!-- Always-visible credential fields. We keep them in DOM during 2FA so a
           browser password manager doesn't lose its association mid-flow. -->
      <div class="fh-field">
        <label class="fh-field-label" for="login-email">{{ $t('common.email') }}</label>
        <input
          id="login-email"
          v-model="email"
          class="fh-field-input"
          type="email"
          autocomplete="username"
          :placeholder="$t('login.email_placeholder')"
          required
          :disabled="mode !== 'creds'"
        />
      </div>

      <div class="fh-field">
        <label class="fh-field-label" for="login-password">{{ $t('common.password') }}</label>
        <input
          id="login-password"
          v-model="password"
          class="fh-field-input"
          type="password"
          autocomplete="current-password"
          :placeholder="$t('login.password_placeholder')"
          required
          :disabled="mode !== 'creds'"
        />
      </div>

      <!-- TOTP step -->
      <div v-if="mode === 'totp'" class="step-2fa">
        <div class="fh-field">
          <label class="fh-field-label" for="login-totp">{{ $t('login.totp_label') }}</label>
          <input
            id="login-totp"
            ref="totpInputRef"
            v-model="totpCode"
            class="fh-field-input fh-field-mono"
            inputmode="numeric"
            pattern="[0-9 ]*"
            maxlength="9"
            :placeholder="$t('login.totp_placeholder')"
            autocomplete="one-time-code"
            required
          />
          <span class="fh-field-help">{{ $t('login.totp_help') }}</span>
        </div>
        <div class="step-2fa-alts">
          <button
            v-if="passkeySupported"
            type="button"
            class="fh-btn fh-btn-ghost passkey-btn"
            :disabled="submitting"
            @click="tryPasskey"
          >
            {{ $t('login.use_passkey') }} <span aria-hidden="true">→</span>
          </button>
          <button type="button" class="fh-btn-text recovery-toggle" @click="mode = 'recovery'">
            {{ $t('login.use_recovery') }}
          </button>
        </div>
      </div>

      <!-- Recovery step -->
      <div v-if="mode === 'recovery'" class="step-2fa">
        <div class="fh-field">
          <label class="fh-field-label" for="login-recovery">{{ $t('login.recovery_label') }}</label>
          <input
            id="login-recovery"
            ref="recoveryInputRef"
            v-model="recoveryCode"
            class="fh-field-input fh-field-mono"
            :placeholder="$t('login.recovery_placeholder')"
            required
          />
          <span class="fh-field-help">{{ $t('login.recovery_help') }}</span>
        </div>
        <button type="button" class="fh-btn-text recovery-toggle" @click="mode = 'totp'">
          {{ $t('login.use_totp_back') }}
        </button>
      </div>

      <div v-if="error" class="fh-notice" data-tone="error" role="alert">{{ error }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="submitting">
          {{ $t(submitLabel) }} <span aria-hidden="true">→</span>
        </button>
        <RouterLink to="/forgot-password" class="forgot">
          {{ $t('login.forgot') }}
        </RouterLink>
      </div>
    </form>
  </AuthCanvas>
</template>

<style scoped>
.form {
  margin-top: var(--fh-space-5);
}

.oidc-btns {
  margin-top: var(--fh-space-5);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.oidc-btn {
  width: 100%;
  justify-content: center;
}

.oidc-rule {
  position: relative;
  margin: var(--fh-space-4) 0 0;
  text-align: center;
}

.oidc-rule::before {
  content: attr(data-label);
  display: inline-block;
  position: absolute;
  top: -8px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--fh-paper);
  padding: 0 var(--fh-space-2);
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--fh-subtle);
}

.step-2fa {
  border-top: 1px solid var(--fh-hairline);
  padding-top: var(--fh-space-3);
  margin-top: var(--fh-space-3);
}

.step-2fa-alts {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  align-items: flex-start;
  margin-top: var(--fh-space-3);
}

.passkey-btn {
  width: 100%;
  justify-content: center;
}

.recovery-toggle {
  font-size: var(--fh-text-body-sm);
}

.actions {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--fh-space-3);
  margin-top: var(--fh-space-4);
}

.forgot {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  text-decoration: none;
  color: var(--fh-subtle);
  transition: color var(--fh-duration-fast) var(--fh-easing);
}

.forgot:hover {
  color: var(--fh-accent);
}
</style>
