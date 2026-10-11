<script setup lang="ts">
  import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
  import { useI18n } from 'vue-i18n'
  import { useRoute, useRouter } from 'vue-router'

  import AuthCanvas from '@/components/AuthCanvas.vue'
  import { asEnvelope } from '@/api/client'
  import { oidcStartUrl, type PublicProvider } from '@/api/oidc'
  import { useApiError } from '@/composables/useApiError'
  import { effectiveLandingPath } from '@/composables/useEffectiveLanding'
  import { POST_LOGIN_REDIRECT_KEY } from '@/router/postLoginRedirect'
  import { isConditionalMediationAvailable, isWebAuthnSupported } from '@/composables/useWebAuthn'
  import { useAuthStore } from '@/stores/auth'
  import { useSiteStore } from '@/stores/site'

  const auth = useAuthStore()
  const router = useRouter()
  const route = useRoute()
  const { describe } = useApiError()
  const { t, te } = useI18n()

  // 'passkey': the password was right, and the account finishes signing in
  // with its passkey (passkey sign-in on, no TOTP - WEBAUTHN_REQUIRED).
  type Mode = 'creds' | 'code' | 'passkey'

  const email = ref('')
  const password = ref('')
  // One second-factor field that accepts EITHER a 6-digit TOTP code or a
  // recovery code (formatted XXXX-XXXX). The two shapes never collide, so
  // onSubmit routes to the right endpoint - the user never has to choose.
  const code = ref('')
  const mode = ref<Mode>('creds')
  const error = ref<string | null>(null)
  const submitting = ref(false)

  const codeInputRef = ref<HTMLInputElement | null>(null)

  // Show the "Use passkey" button only when the browser supports
  // WebAuthn AND the user is past the password step. The browser
  // support check is computed once on script setup; navigator API
  // availability doesn't change at runtime.
  const passkeySupported = isWebAuthnSupported()

  const site = useSiteStore()
  const providers = computed<PublicProvider[]>(() => site.providers)
  const motdText = computed<string>(() => site.motd?.text ?? '')
  // Passkey sign-in (v2.27.0): a passkey alone, offered only while an admin
  // has it switched on.
  const passkeySignin = computed(() => site.passkeySignin && passkeySupported)
  const altSignIn = computed(
    () => mode.value === 'creds' && (passkeySignin.value || providers.value.length > 0),
  )

  // The email field's autofill lists this site's passkeys while a request with
  // `mediation: 'conditional'` is waiting. It is re-armed before the server's
  // five-minute challenge lapses, so a passkey picked after a long pause still
  // has a live challenge to answer, and aborted before any other passkey
  // request - the browser runs one at a time.
  const AUTOFILL_REARM_MS = 4 * 60 * 1000
  let autofill: AbortController | null = null
  let rearmTimer: ReturnType<typeof setTimeout> | null = null
  let leaving = false

  function stopAutofill() {
    if (rearmTimer !== null) clearTimeout(rearmTimer)
    rearmTimer = null
    autofill?.abort()
    autofill = null
  }

  async function armAutofill() {
    stopAutofill()
    if (!(await isConditionalMediationAvailable())) return
    // Checked after the await: the page may have moved on, a button-started
    // request may be running, and another call may have armed meanwhile.
    stopAutofill()
    if (leaving || submitting.value || !passkeySignin.value || mode.value !== 'creds') return
    const controller = new AbortController()
    autofill = controller
    rearmTimer = setTimeout(() => void armAutofill(), AUTOFILL_REARM_MS)
    let picked = false
    try {
      await auth.loginWithPasskeyOnly({
        mediation: 'conditional',
        signal: controller.signal,
        onPicked: () => {
          // Signing in now; a re-arm mid-request would only start a new wait.
          picked = true
          if (rearmTimer !== null) clearTimeout(rearmTimer)
          rearmTimer = null
        },
      })
    } catch (e) {
      // Replaced, or the page is going away: nothing to report.
      if (controller.signal.aborted) return
      // Only the answer to a passkey the user picked is worth showing, and
      // only then is it offered again at once. A refused start (switched off,
      // rate limited, offline) or a browser that refuses outright just leaves
      // the autofill empty until the timer - re-arming from here would loop on
      // a failure that takes no click.
      if (picked) {
        error.value = describe(e)
        void armAutofill()
      }
      return
    }
    stopAutofill()
    await router.push(redirectTo.value)
  }

  async function signInWithPasskey() {
    stopAutofill()
    error.value = null
    submitting.value = true
    try {
      await auth.loginWithPasskeyOnly()
      await router.push(redirectTo.value)
      return
    } catch (e) {
      error.value =
        e instanceof DOMException && e.name === 'NotAllowedError'
          ? t('login.passkey_cancelled')
          : describe(e)
    } finally {
      submitting.value = false
    }
    void armAutofill()
  }

  watch(passkeySignin, (on) => {
    if (on) void armAutofill()
    else stopAutofill()
  })

  onBeforeUnmount(() => {
    leaving = true
    stopAutofill()
  })

  function onProviderClick(p: PublicProvider) {
    // Carry the deep link across the IdP round-trip. The OIDC callback is a
    // BACKEND redirect that always lands on `/`, so a user who followed a link to
    // a share, got bounced to /login?redirect=/share/abc and signed in with SSO
    // arrived at the dashboard with no idea what they had been trying to open -
    // while the password form honoured the same parameter (audit 2026-07-30,
    // fe-auth-7). sessionStorage, not localStorage: it must not outlive the tab.
    const target = route.query.redirect
    if (typeof target === 'string' && target.startsWith('/') && !target.startsWith('//')) {
      try {
        window.sessionStorage?.setItem(POST_LOGIN_REDIRECT_KEY, target)
      } catch {
        /* storage unavailable - fall back to landing on the default page */
      }
    }
    window.location.href = oidcStartUrl(p.id)
  }

  watch(mode, async (m) => {
    await nextTick()
    if (m === 'code') codeInputRef.value?.focus()
  })

  // A failed SSO callback redirects here with ?oidc_error=CODE. Before that the
  // backend answered the IdP's browser redirect with a raw JSON error body on an
  // /api/ URL: no nav, no retry, and for OIDC_NO_ACCOUNT - the expected outcome
  // for anyone without an invite - no explanation either.
  onMounted(() => {
    void armAutofill()
    // The second-factor interstitial sends `?expired=1` when its five-minute
    // pending token lapsed. This page never read it, so the user landed on a
    // bare form with no idea why they were back here.
    if (route.query.expired === '1') {
      error.value = t('login.pending_expired')
      void router.replace({ path: route.path, query: {} })
      return
    }
    const failed = route.query.oidc_error
    if (typeof failed !== 'string' || !failed) return
    const key = `errors.${failed}`
    error.value = te(key) ? t(key) : t('errors.generic')
    void router.replace({ path: route.path, query: {} })
  })

  const submitLabel = computed(() => {
    if (submitting.value) return 'login.submitting'
    return mode.value === 'passkey' ? 'login.passkey_continue' : 'login.submit'
  })
  // Honour an explicit ?redirect=... ("back to where I was going");
  // otherwise compute the user's effective landing once they've logged
  // in. Login flow: auth.login() populates auth.user, so by the time we
  // evaluate `redirectTo` the MeResponse is in the store.
  const redirectTo = computed(() => {
    const explicit = route.query.redirect as string | undefined
    if (explicit) return explicit
    return effectiveLandingPath(auth.user)
  })

  // A TOTP code is exactly six digits; a recovery code is XXXX-XXXX (letters +
  // a hyphen). They never collide, so we route on shape alone.
  function isTotpShape(v: string): boolean {
    return /^\d{6}$/.test(v.replace(/\s+/g, ''))
  }

  async function onSubmit() {
    if (mode.value === 'passkey') {
      await tryPasskey()
      return
    }
    stopAutofill()
    error.value = null
    submitting.value = true
    let rearm = false
    try {
      if (mode.value === 'creds') {
        // Step 1: email + password only. If 2FA is on, the server answers
        // TOTP_REQUIRED and we reveal the code step below (no penalty - the
        // password was already verified).
        await auth.login(email.value, password.value)
      } else {
        const entered = code.value.trim()
        if (isTotpShape(entered)) {
          await auth.login(email.value, password.value, entered.replace(/\s+/g, ''))
        } else {
          await auth.loginWithRecovery(email.value, password.value, entered)
        }
      }
      await router.push(redirectTo.value)
    } catch (e) {
      const env = asEnvelope(e)
      if (env?.code === 'TOTP_REQUIRED') {
        mode.value = 'code'
        error.value = null
      } else if (env?.code === 'WEBAUTHN_REQUIRED') {
        // The password was right; the passkey finishes it. A button, not an
        // automatic prompt: browsers want a fresh click before one opens.
        if (passkeySupported) {
          mode.value = 'passkey'
          error.value = null
        } else {
          error.value = t('login.passkey_unsupported')
        }
      } else {
        error.value = describe(e)
        // Wrong code → clear it so the next attempt starts fresh.
        if (mode.value === 'code') code.value = ''
        else rearm = true
      }
    } finally {
      submitting.value = false
    }
    if (rearm) void armAutofill()
  }

  // Passkey-as-second-factor login: from the TOTP step, or the passkey step
  // when the password alone could not finish. The backend returns a WebAuthn
  // challenge after validating the password; the browser prompts the user,
  // signs the challenge, and we ship the assertion back to mint the same JWT +
  // refresh cookie as the password+TOTP path.
  async function tryPasskey() {
    error.value = null
    submitting.value = true
    try {
      const result = await auth.loginWithPasskey(email.value, password.value)
      if (result.status === 'pending_2fa') {
        // The assertion carried no user verification, so the passkey does not
        // count as the second factor: hand the pending token to the same
        // interstitial the SSO flow uses. It strips the token from the URL.
        await router.push({ name: 'login-2fa', query: { pending: result.pendingToken } })
        return
      }
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
      v-if="motdText"
      class="motd-banner fh-rise"
      data-stagger="2"
      role="note"
      :aria-label="$t('login.motd_aria')"
    >
      {{ motdText }}
    </div>

    <div v-if="altSignIn" class="oidc-btns fh-rise" data-stagger="3">
      <button
        v-if="passkeySignin"
        type="button"
        class="fh-btn fh-btn-ghost oidc-btn"
        data-testid="passkey-signin"
        :disabled="submitting"
        @click="signInWithPasskey"
      >
        {{ $t('login.passkey_signin') }}
        <span aria-hidden="true">→</span>
      </button>
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
      v-if="altSignIn"
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
          :autocomplete="passkeySignin ? 'username webauthn' : 'username'"
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

      <!-- Second-factor step: one field that takes a TOTP *or* a recovery code -->
      <div v-if="mode === 'code'" class="step-2fa">
        <div class="fh-field">
          <label class="fh-field-label" for="login-code">{{ $t('login.code_label') }}</label>
          <input
            id="login-code"
            ref="codeInputRef"
            v-model="code"
            class="fh-field-input fh-field-mono"
            :placeholder="$t('login.code_placeholder')"
            autocomplete="one-time-code"
            required
          />
          <span class="fh-field-help">{{ $t('login.code_help') }}</span>
        </div>
        <div v-if="passkeySupported" class="step-2fa-alts">
          <button
            type="button"
            class="fh-btn fh-btn-ghost passkey-btn"
            :disabled="submitting"
            @click="tryPasskey"
          >
            {{ $t('login.use_passkey') }} <span aria-hidden="true">→</span>
          </button>
        </div>
      </div>

      <!-- Passkey step: the password was right, the passkey finishes it. -->
      <p v-if="mode === 'passkey'" class="step-2fa fh-field-help" data-testid="passkey-step">
        {{ $t('login.passkey_step_help') }}
      </p>

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
  .motd-banner {
    margin-top: var(--fh-space-4);
    padding: var(--fh-space-3);
    background: var(--fh-accent-soft);
    border-left: 2px solid var(--fh-accent);
    border-radius: var(--fh-radius-sm);
    font-size: var(--fh-text-body-sm);
    white-space: pre-wrap;
  }

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
