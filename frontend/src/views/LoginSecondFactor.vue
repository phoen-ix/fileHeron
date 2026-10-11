<script setup lang="ts">
  /* Second factor for a login whose FIRST factor was SSO or a passkey.
   *
   * Neither of those paths used to challenge an enrolled TOTP factor - both
   * minted a full session outright - so turning 2FA on did nothing at all for
   * anyone who signed in that way, while the account page said it was on.
   *
   * The pending token arrives in the query string because the browser is
   * mid-redirect and holds no session yet. It grants nothing on its own, lives
   * five minutes, and this exchange is the only endpoint that accepts it. It is
   * dropped from the URL as soon as it is read so it does not linger in history
   * or get copied out of the address bar.
   *
   * `methods` says what can finish it (v2.27.0): `totp`, and `passkey` while
   * passkey sign-in counts a passkey as 2FA. Absent - an older server - means
   * a code is all it takes. */
  import { computed, onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'
  import { useRoute, useRouter } from 'vue-router'

  import { asEnvelope } from '@/api/client'
  import { useApiError } from '@/composables/useApiError'
  import { isWebAuthnSupported } from '@/composables/useWebAuthn'
  import { useAuthStore } from '@/stores/auth'

  const { t } = useI18n()
  const { describe } = useApiError()
  const route = useRoute()
  const router = useRouter()
  const auth = useAuthStore()

  const pendingToken = ref('')
  const methods = ref<string[]>(['totp'])
  const offersCode = computed(() => methods.value.includes('totp'))
  const offersPasskey = computed(() => methods.value.includes('passkey'))
  const passkeySupported = isWebAuthnSupported()
  const code = ref('')
  const useRecovery = ref(false)
  const submitting = ref(false)
  const errorMsg = ref<string | null>(null)

  onMounted(() => {
    const raw = route.query.pending
    pendingToken.value = typeof raw === 'string' ? raw : ''
    const offered = route.query.methods
    if (typeof offered === 'string' && offered) methods.value = offered.split(',')
    if (!pendingToken.value) {
      router.replace({ name: 'login' })
      return
    }
    // Strip it from the address bar immediately.
    router.replace({ name: 'login-2fa' })
  })

  // The pending token is short-lived by design; when it lapses the only honest
  // thing to say is "start again" rather than leaving the user retrying against
  // a token that can no longer work.
  async function restartIfExpired(err: unknown): Promise<boolean> {
    const codeStr = asEnvelope(err)?.code
    if (codeStr !== 'PENDING_2FA_EXPIRED' && codeStr !== 'INVALID_TOKEN') return false
    await router.replace({ name: 'login', query: { expired: '1' } })
    return true
  }

  async function onSubmit() {
    const entered = code.value.trim()
    if (!entered || submitting.value) return
    submitting.value = true
    errorMsg.value = null
    try {
      await auth.completeSecondFactor(
        pendingToken.value,
        useRecovery.value ? { recoveryCode: entered } : { totpCode: entered.replace(/\s+/g, '') },
      )
      await router.replace({ name: 'home' })
    } catch (err) {
      if (await restartIfExpired(err)) return
      errorMsg.value = describe(err)
      code.value = ''
    } finally {
      submitting.value = false
    }
  }

  async function usePasskey() {
    if (submitting.value) return
    submitting.value = true
    errorMsg.value = null
    try {
      await auth.completeSecondFactorWithPasskey(pendingToken.value)
      await router.replace({ name: 'home' })
    } catch (err) {
      if (await restartIfExpired(err)) return
      errorMsg.value =
        err instanceof DOMException && err.name === 'NotAllowedError'
          ? t('login.passkey_cancelled')
          : describe(err)
    } finally {
      submitting.value = false
    }
  }

  function toggleRecovery() {
    useRecovery.value = !useRecovery.value
    code.value = ''
    errorMsg.value = null
  }
</script>

<template>
  <div class="twofa-page">
    <h1 class="fh-eyebrow">{{ t('login_2fa.eyebrow') }}</h1>
    <p class="fh-field-help">
      <template v-if="!offersCode">{{ t('login_2fa.passkey_help') }}</template>
      <template v-else>
        {{ useRecovery ? t('login_2fa.recovery_help') : t('login_2fa.totp_help') }}
      </template>
    </p>

    <form v-if="offersCode" class="twofa-form" @submit.prevent="onSubmit">
      <label class="fh-field">
        <span class="fh-field-label">
          {{ useRecovery ? t('login_2fa.recovery_label') : t('login_2fa.totp_label') }}
        </span>
        <input
          v-model="code"
          class="fh-field-input fh-field-mono"
          :type="useRecovery ? 'text' : 'text'"
          :inputmode="useRecovery ? 'text' : 'numeric'"
          :autocomplete="useRecovery ? 'off' : 'one-time-code'"
          autofocus
          :disabled="submitting"
        />
      </label>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="submitting || !code.trim()">
          {{ submitting ? t('common.loading') : t('login_2fa.submit') }}
        </button>
        <button type="button" class="fh-btn-text" :disabled="submitting" @click="toggleRecovery">
          {{ useRecovery ? t('login_2fa.use_totp') : t('login_2fa.use_recovery') }}
        </button>
      </div>
    </form>

    <div v-if="offersPasskey && passkeySupported" class="passkey-alt">
      <button
        type="button"
        class="fh-btn"
        :class="{ 'fh-btn-ghost': offersCode }"
        data-testid="twofa-passkey"
        :disabled="submitting"
        @click="usePasskey"
      >
        {{ offersCode ? t('login_2fa.use_passkey_instead') : t('login_2fa.use_passkey') }}
      </button>
    </div>
    <div v-else-if="!offersCode" class="fh-notice" data-tone="error" role="alert">
      {{ t('login_2fa.passkey_unsupported') }}
    </div>

    <div v-if="errorMsg" class="fh-notice errors" data-tone="error" role="alert">
      {{ errorMsg }}
    </div>
  </div>
</template>

<style scoped>
  .twofa-page {
    max-width: 26rem;
    margin: 4rem auto;
    padding: 0 1rem;
  }
  .twofa-form {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    margin-top: 1.5rem;
  }
  .actions {
    display: flex;
    align-items: center;
    gap: 1rem;
  }
  .passkey-alt,
  .errors {
    margin-top: 1rem;
  }
</style>
