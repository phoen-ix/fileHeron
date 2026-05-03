<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import * as twoFaApi from '@/api/twoFactor'
import { useApiError } from '@/composables/useApiError'
import { effectiveLandingPath } from '@/composables/useEffectiveLanding'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import type { TotpStatusResponse } from '@/types/api'

const ui = useUiStore()
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const { describe } = useApiError()
const { t } = useI18n()

type Stage = 'loading' | 'inactive-intro' | 'qr' | 'codes' | 'active' | 'disable'
const stage = ref<Stage>('loading')

// True when the active 2FA policy requires this user to enrol but
// they haven't yet. The page auto-launches the QR step and hides
// "back" / "cancel" affordances when this is true so the user can
// only progress forward through the wizard.
const forced = computed(() => auth.user?.requires_2fa === true)

const status = ref<TotpStatusResponse | null>(null)
const setup = ref<{ secret_b32: string; otpauth_uri: string; qr_svg: string } | null>(null)
const code = ref('')
const codes = ref<string[]>([])
const codesAcknowledged = ref(false)

const disablePassword = ref('')
const disableCode = ref('')

const submitting = ref(false)
const error = ref<string | null>(null)

const formattedSecret = computed(() => {
  const s = setup.value?.secret_b32 ?? ''
  return s.match(/.{1,4}/g)?.join(' ') ?? s
})

onMounted(async () => {
  await refreshStatus()
  // Forced mode: skip the inactive-intro click — the user is here
  // because policy demands enrolment, so show the QR immediately.
  if (forced.value && stage.value === 'inactive-intro') {
    await startSetup()
  }
})

async function refreshStatus() {
  try {
    const r = await twoFaApi.getStatus()
    status.value = r.data
    stage.value = r.data.enabled ? 'active' : 'inactive-intro'
  } catch (e) {
    error.value = describe(e)
  }
}

async function startSetup() {
  error.value = null
  submitting.value = true
  try {
    const r = await twoFaApi.beginSetup()
    setup.value = r.data
    stage.value = 'qr'
  } catch (e) {
    error.value = describe(e)
  } finally {
    submitting.value = false
  }
}

async function confirmEnable() {
  error.value = null
  submitting.value = true
  try {
    const r = await twoFaApi.enable({ code: code.value })
    codes.value = r.data.recovery_codes
    stage.value = 'codes'
    code.value = ''
  } catch (e) {
    error.value = describe(e)
  } finally {
    submitting.value = false
  }
}

async function copyCodes() {
  await navigator.clipboard.writeText(codes.value.join('\n'))
  ui.pushToast(t('twofa.recovery_codes_copied'), 'success')
}

async function finishCodes() {
  if (!codesAcknowledged.value) return
  // Refresh /me so requires_2fa flips false in the auth store; the
  // route guard will then let the user navigate freely.
  await auth.refreshMe()

  // If we got here via the forced flow, return to whatever the user
  // was originally trying to reach (or fall back to their effective
  // landing path).
  const redirect = route.query.redirect
  if (typeof redirect === 'string' && redirect) {
    await router.replace(redirect)
    return
  }
  if (auth.user?.requires_2fa === false && forced.value === false) {
    // Wasn't forced — this is a normal voluntary enrolment from
    // /account; settle into the active-management view.
    await refreshStatus()
    return
  }
  // Forced enrolment finishing without an explicit redirect: send
  // the user to their effective landing page.
  await router.replace(effectiveLandingPath(auth.user))
}

async function regenerateCodes() {
  error.value = null
  submitting.value = true
  try {
    const r = await twoFaApi.regenerateRecoveryCodes({
      password: disablePassword.value,
      code_or_recovery: disableCode.value,
    })
    codes.value = r.data.recovery_codes
    codesAcknowledged.value = false
    stage.value = 'codes'
    disablePassword.value = ''
    disableCode.value = ''
  } catch (e) {
    error.value = describe(e)
  } finally {
    submitting.value = false
  }
}

async function disable() {
  error.value = null
  submitting.value = true
  try {
    await twoFaApi.disable({
      password: disablePassword.value,
      code_or_recovery: disableCode.value,
    })
    disablePassword.value = ''
    disableCode.value = ''
    ui.pushToast(t('twofa.disabled_toast'), 'success')
    await refreshStatus()
  } catch (e) {
    error.value = describe(e)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="fh-prose twofa">
    <span class="fh-eyebrow fh-rise" data-stagger="1">/ account / 2fa</span>
    <h1 class="fh-display-md fh-rise" data-stagger="2">{{ $t('twofa.title') }}</h1>

    <!-- Inactive intro -->
    <template v-if="stage === 'inactive-intro'">
      <p class="fh-rise" data-stagger="3">{{ $t('twofa.intro') }}</p>
      <button class="fh-btn fh-rise" data-stagger="3" :disabled="submitting" @click="startSetup">
        {{ $t('account.twofa_setup_cta') }} <span aria-hidden="true">→</span>
      </button>
    </template>

    <!-- QR + verification step -->
    <section v-else-if="stage === 'qr' && setup" class="qr-step">
      <div v-if="forced" class="fh-notice forced-banner" data-tone="info">
        {{ $t('twofa.forced_banner') }}
      </div>
      <p>{{ $t('twofa.intro') }}</p>

      <div class="qr-pair">
        <!-- v-html trusts the backend-rendered SVG. The qr_svg field
             on TotpSetupResponse is generated server-side from the
             secret — NEVER allow user-supplied content into that
             field, or this becomes a stored XSS sink. -->
        <div class="qr-svg" v-html="setup.qr_svg" />
        <div class="qr-secret">
          <span class="fh-eyebrow">{{ $t('twofa.manual_secret_label') }}</span>
          <code class="secret-mono">{{ formattedSecret }}</code>
        </div>
      </div>

      <form class="verify-form" @submit.prevent="confirmEnable">
        <div class="fh-field">
          <label class="fh-field-label" for="totp-verify">{{ $t('twofa.verify_label') }}</label>
          <input
            id="totp-verify"
            v-model="code"
            class="fh-field-input fh-field-mono"
            inputmode="numeric"
            pattern="[0-9 ]*"
            maxlength="9"
            :placeholder="$t('twofa.verify_placeholder')"
            autocomplete="one-time-code"
            required
          />
          <span class="fh-field-help">{{ $t('twofa.verify_help') }}</span>
        </div>

        <div v-if="error" class="fh-notice" data-tone="error">{{ error }}</div>

        <button class="fh-btn" :disabled="submitting">
          {{ $t('twofa.enable_submit') }} <span aria-hidden="true">→</span>
        </button>
      </form>
    </section>

    <!-- Recovery codes one-time display -->
    <section v-else-if="stage === 'codes'" class="codes-step">
      <h2 class="codes-h2">{{ $t('twofa.saved_codes_title') }}</h2>
      <p>{{ $t('twofa.saved_codes_intro') }}</p>

      <div class="codes-grid">
        <code v-for="c in codes" :key="c" class="code-cell">{{ c }}</code>
      </div>

      <div class="codes-actions">
        <button type="button" class="fh-btn-ghost fh-btn" @click="copyCodes">
          {{ $t('twofa.saved_codes_copy') }}
        </button>
        <label class="codes-confirm">
          <input v-model="codesAcknowledged" type="checkbox" />
          <span>{{ $t('twofa.saved_codes_confirm') }}</span>
        </label>
        <button class="fh-btn" :disabled="!codesAcknowledged" @click="finishCodes">
          {{ $t('common.continue') }} <span aria-hidden="true">→</span>
        </button>
      </div>
    </section>

    <!-- Active management -->
    <section v-else-if="stage === 'active' && status" class="active-step">
      <p class="active-line">
        <span class="dot" data-on />
        <strong>{{ $t('twofa.active_title') }}</strong>
        {{ $t('twofa.active_intro') }}
      </p>

      <p class="fh-field-help">
        {{
          $t('account.twofa_recovery_remaining', { n: status.recovery_codes_remaining })
        }}
      </p>

      <hr class="fh-rule" />

      <h2 class="active-h2">{{ $t('twofa.regenerate_cta') }}</h2>
      <form class="manage-form" @submit.prevent="regenerateCodes">
        <div class="fh-field">
          <label class="fh-field-label">{{ $t('common.current_password') }}</label>
          <input
            v-model="disablePassword"
            class="fh-field-input"
            type="password"
            autocomplete="current-password"
            required
          />
        </div>
        <div class="fh-field">
          <label class="fh-field-label">{{ $t('twofa.code_or_recovery_label') }}</label>
          <input
            v-model="disableCode"
            class="fh-field-input fh-field-mono"
            autocomplete="one-time-code"
            required
          />
        </div>
        <button type="submit" class="fh-btn-ghost fh-btn" :disabled="submitting">
          {{ $t('twofa.regenerate_cta') }}
        </button>
      </form>

      <hr class="fh-rule" />

      <h2 class="active-h2 danger">{{ $t('twofa.danger_section') }}</h2>
      <p>{{ $t('twofa.disable_intro') }}</p>
      <button type="button" class="fh-btn-danger fh-btn" @click="stage = 'disable'">
        {{ $t('twofa.disable_cta') }}
      </button>
    </section>

    <!-- Disable confirmation -->
    <section v-else-if="stage === 'disable'" class="active-step">
      <h2 class="active-h2 danger">{{ $t('twofa.disable_cta') }}</h2>
      <p>{{ $t('twofa.disable_intro') }}</p>
      <form class="manage-form" @submit.prevent="disable">
        <div class="fh-field">
          <label class="fh-field-label">{{ $t('common.current_password') }}</label>
          <input
            v-model="disablePassword"
            class="fh-field-input"
            type="password"
            autocomplete="current-password"
            required
          />
        </div>
        <div class="fh-field">
          <label class="fh-field-label">{{ $t('twofa.code_or_recovery_label') }}</label>
          <input
            v-model="disableCode"
            class="fh-field-input fh-field-mono"
            autocomplete="one-time-code"
            required
          />
        </div>

        <div v-if="error" class="fh-notice" data-tone="error">{{ error }}</div>

        <div style="display: flex; gap: var(--fh-space-3); align-items: baseline">
          <button type="submit" class="fh-btn-danger fh-btn" :disabled="submitting">
            {{ $t('twofa.disable_cta') }}
          </button>
          <button type="button" class="fh-btn-text" @click="stage = 'active'">
            {{ $t('common.cancel') }}
          </button>
        </div>
      </form>
    </section>

    <!-- Loading state -->
    <p v-else class="fh-field-help">{{ $t('common.loading') }}</p>
  </div>
</template>

<style scoped>
.forced-banner {
  margin-bottom: var(--fh-space-3);
}

.qr-pair {
  display: flex;
  align-items: flex-start;
  gap: var(--fh-space-5);
  margin: var(--fh-space-4) 0;
  flex-wrap: wrap;
}

.qr-svg {
  flex-shrink: 0;
  background: var(--fh-paper-raised);
  padding: var(--fh-space-2);
  border: 1px solid var(--fh-hairline-strong);
}

.qr-svg :deep(svg) {
  display: block;
  width: 200px;
  height: 200px;
}

.qr-secret {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.secret-mono {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-md);
  letter-spacing: 0.08em;
  background: var(--fh-paper-sunk);
  padding: var(--fh-space-2) var(--fh-space-3);
  border: 1px solid var(--fh-hairline);
  word-break: break-all;
  user-select: all;
}

.verify-form {
  margin-top: var(--fh-space-4);
}

.codes-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
  letter-spacing: -0.01em;
  margin: 0 0 var(--fh-space-2);
}

.codes-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--fh-space-2);
  margin: var(--fh-space-4) 0;
}

.code-cell {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-md);
  background: var(--fh-paper-raised);
  border: 1px solid var(--fh-hairline-strong);
  padding: var(--fh-space-2) var(--fh-space-3);
  text-align: center;
  letter-spacing: 0.06em;
  user-select: all;
}

.codes-actions {
  display: flex;
  align-items: center;
  gap: var(--fh-space-3);
  flex-wrap: wrap;
}

.codes-confirm {
  display: inline-flex;
  align-items: center;
  gap: var(--fh-space-2);
  font-size: var(--fh-text-body-sm);
  color: var(--fh-subtle);
  cursor: pointer;
}

.active-step {
  margin-top: var(--fh-space-4);
}

.active-line {
  display: flex;
  align-items: baseline;
  gap: var(--fh-space-2);
}

.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--fh-subtle-soft);
}
.dot[data-on] {
  background: var(--fh-success);
}

.active-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
  letter-spacing: -0.01em;
  margin: 0 0 var(--fh-space-3);
}

.active-h2.danger {
  color: var(--fh-danger);
}

.manage-form {
  max-width: 360px;
}
</style>
