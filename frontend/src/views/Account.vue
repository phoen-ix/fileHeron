<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import * as accountApi from '@/api/account'
import * as twoFaApi from '@/api/twoFactor'
import SectionQuickNav, {
  type QuickNavSection,
} from '@/components/SectionQuickNav.vue'
import ApiTokenPanel from '@/components/ApiTokenPanel.vue'
import NotificationPreferences from '@/components/NotificationPreferences.vue'
import OIDCConnectPanel from '@/components/OIDCConnectPanel.vue'
import PasswordStrength from '@/components/PasswordStrength.vue'
import SessionRow from '@/components/SessionRow.vue'
import WebAuthnPanel from '@/components/WebAuthnPanel.vue'
import { useApiError } from '@/composables/useApiError'
import { useScrollSpy } from '@/composables/useScrollSpy'
import { setLocale } from '@/i18n'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import type { Locale, SessionRecord, TotpStatusResponse } from '@/types/api'

const auth = useAuthStore()
const ui = useUiStore()
const { describe } = useApiError()
const { t } = useI18n()

/* --- profile (display name + locale + landing page) -------------------- */
const displayName = ref('')
const locale = ref<Locale>('en')
const landingPage = ref<string | null>(null)
const nameSaving = ref(false)

const nameDirty = computed(() => {
  const trimmed = displayName.value.trim()
  return trimmed !== '' && trimmed !== auth.user?.display_name
})

async function saveDisplayName() {
  const trimmed = displayName.value.trim()
  if (!trimmed || trimmed === auth.user?.display_name) return
  nameSaving.value = true
  try {
    await accountApi.updateDisplayName(trimmed)
    await auth.refreshMe()
    displayName.value = auth.user?.display_name ?? trimmed
    ui.pushToast(t('account.display_name_saved'), 'success')
  } catch (e) {
    ui.pushToast(
      t('account.display_name_save_failed') + ' ' + describe(e),
      'error',
    )
  } finally {
    nameSaving.value = false
  }
}

/* --- password change ---------------------------------------------------- */
const currentPw = ref('')
const newPw = ref('')
const pwSubmitting = ref(false)
const pwError = ref<string | null>(null)

/* --- 2FA + sessions ----------------------------------------------------- */
const totpStatus = ref<TotpStatusResponse | null>(null)
const sessions = ref<SessionRecord[]>([])

onMounted(async () => {
  if (auth.user) {
    displayName.value = auth.user.display_name
    locale.value = auth.user.locale
    landingPage.value = auth.user.default_landing_page
  }
  await Promise.all([loadTotp(), loadSessions()])
})

async function changeLandingPage(value: string | null) {
  if (value === landingPage.value) return
  const previous = landingPage.value
  landingPage.value = value
  try {
    await accountApi.updateDefaultLandingPage(value)
    await auth.refreshMe()
    ui.pushToast(t('account.landing.saved_toast'), 'success')
  } catch (e) {
    landingPage.value = previous
    ui.pushToast(
      t('account.landing.save_failed') + ' ' + describe(e),
      'error',
    )
  }
}

async function loadTotp() {
  try {
    const r = await twoFaApi.getStatus()
    totpStatus.value = r.data
  } catch {
    /* non-fatal */
  }
}

async function loadSessions() {
  try {
    const r = await accountApi.listSessions()
    sessions.value = r.data.items
  } catch {
    /* non-fatal */
  }
}

async function changePassword() {
  pwError.value = null
  pwSubmitting.value = true
  try {
    await accountApi.changePassword({ current_password: currentPw.value, new_password: newPw.value })
    currentPw.value = ''
    newPw.value = ''
    ui.pushToast(t('account.password_changed_toast'), 'success')
    // The current refresh cookie remains valid since change_password revokes
    // all of the user's tokens — but the backend deliberately does not include
    // a fresh cookie in the response. Force a refresh round-trip:
    await auth.refreshMe()
  } catch (e) {
    pwError.value = describe(e)
  } finally {
    pwSubmitting.value = false
  }
}

async function revokeSession(id: number) {
  try {
    await accountApi.revokeSession(id)
    sessions.value = sessions.value.filter((s) => s.id !== id)
    ui.pushToast(t('account.session_revoked_toast'), 'success')
  } catch (e) {
    ui.pushToast(describe(e), 'error')
  }
}

async function changeLocale(l: Locale) {
  if (locale.value === l) return
  const previous = locale.value
  // Optimistic UI: flip the language immediately. setLocale also writes
  // to localStorage so a subsequent logout/anonymous visit keeps the choice.
  locale.value = l
  setLocale(l)
  try {
    await accountApi.updateLocale(l)
    await auth.refreshMe()
    ui.pushToast(t('account.locale_saved'), 'success')
  } catch (e) {
    locale.value = previous
    setLocale(previous)
    ui.pushToast(t('account.locale_save_failed') + ' ' + describe(e), 'error')
  }
}

/* --- floating quick-nav with scroll-spy -------------------------------- */
// The list is unconditional: child panels render their own content, and
// useScrollSpy quietly skips ids whose elements aren't in the DOM yet.
// When those panels mount their content later (OIDC after a config fetch,
// WebAuthn after browser-support detection), the watch in the composable
// re-binds the observer.
const sections = computed<QuickNavSection[]>(() => [
  { id: 'profile', labelKey: 'account.section_profile' },
  { id: 'password', labelKey: 'account.section_password' },
  { id: '2fa', labelKey: 'account.section_2fa' },
  { id: 'sessions', labelKey: 'account.section_sessions' },
  { id: 'api-tokens', labelKey: 'api_tokens.title' },
  { id: 'notifications', labelKey: 'notif_prefs.title' },
  { id: 'webauthn', labelKey: 'webauthn.title' },
  { id: 'oidc', labelKey: 'account_oidc.section_title' },
])

const sectionIds = computed(() => sections.value.map((s) => s.id))
const { active, lockTo } = useScrollSpy(() => sectionIds.value, {
  topOffsetPx: 80,
  bottomOffsetVh: 60,
})

function jumpTo(id: string) {
  lockTo(id)
  document.getElementById(id)?.scrollIntoView({
    behavior: 'smooth',
    block: 'start',
  })
}
</script>

<template>
  <div class="account-layout">
    <div class="account-prose fh-prose">
    <span class="fh-eyebrow fh-rise" data-stagger="1">{{ $t('account.title') }}</span>
    <h1 class="fh-display-md fh-rise" data-stagger="2">{{ auth.user?.display_name }}</h1>
    <p class="fh-rise email-line" data-stagger="2">
      <span class="fh-mono">{{ auth.user?.email }}</span>
      <span class="role-pill">{{ auth.user?.role }}</span>
    </p>

    <hr class="fh-rule" />

    <!-- Profile -->
    <section id="profile" class="account-section fh-rise" data-stagger="3">
      <h2 class="account-h2">{{ $t('account.section_profile') }}</h2>
      <div class="fh-field">
        <label class="fh-field-label" for="acc-display-name">{{ $t('common.display_name') }}</label>
        <input
          id="acc-display-name"
          class="fh-field-input"
          v-model="displayName"
          maxlength="120"
          @keydown.enter.prevent="saveDisplayName"
        />
        <span class="fh-field-help">{{ $t('account.display_name_help') }}</span>
        <div v-if="nameDirty" class="display-name-actions">
          <button
            type="button"
            class="fh-btn"
            :disabled="nameSaving"
            @click="saveDisplayName"
          >
            {{ nameSaving ? $t('common.loading') : $t('common.save') }}
          </button>
          <button
            type="button"
            class="fh-btn-text"
            :disabled="nameSaving"
            @click="displayName = auth.user?.display_name ?? ''"
          >
            {{ $t('common.cancel') }}
          </button>
        </div>
      </div>

      <div class="fh-field">
        <label class="fh-field-label">{{ $t('common.language') }}</label>
        <div class="locale-pick">
          <button
            type="button"
            class="locale-opt"
            :class="{ active: locale === 'en' }"
            @click="changeLocale('en')"
          >
            English
          </button>
          <button
            type="button"
            class="locale-opt"
            :class="{ active: locale === 'de' }"
            @click="changeLocale('de')"
          >
            Deutsch
          </button>
        </div>
        <span class="fh-field-help">{{ $t('account.language_help') }}</span>
      </div>

      <div class="fh-field">
        <label class="fh-field-label" for="acc-landing">
          {{ $t('account.landing.label') }}
        </label>
        <select
          id="acc-landing"
          class="fh-field-input"
          :value="landingPage"
          @change="changeLandingPage(($event.target as HTMLSelectElement).value || null)"
        >
          <option :value="''">{{ $t('account.landing.system_default') }}</option>
          <option
            v-if="auth.user?.home_page_enabled"
            value="home"
          >
            {{ $t('account.landing.home') }}
          </option>
          <option value="outbox">{{ $t('account.landing.outbox') }}</option>
          <option value="inbox">{{ $t('account.landing.inbox') }}</option>
          <option value="share-create">{{ $t('account.landing.share_create') }}</option>
          <option value="account">{{ $t('account.landing.account') }}</option>
        </select>
        <span class="fh-field-help">{{ $t('account.landing.help') }}</span>
      </div>
    </section>

    <!-- Password -->
    <section id="password" class="account-section">
      <h2 class="account-h2">{{ $t('account.section_password') }}</h2>
      <p class="fh-field-help" style="margin-bottom: var(--fh-space-3)">
        {{ $t('account.change_password_help') }}
      </p>
      <form @submit.prevent="changePassword">
        <div class="fh-field">
          <label class="fh-field-label" for="acc-cur">{{ $t('common.current_password') }}</label>
          <input
            id="acc-cur"
            v-model="currentPw"
            class="fh-field-input"
            type="password"
            autocomplete="current-password"
            required
          />
        </div>
        <div class="fh-field">
          <label class="fh-field-label" for="acc-new">{{ $t('common.new_password') }}</label>
          <input
            id="acc-new"
            v-model="newPw"
            class="fh-field-input"
            type="password"
            autocomplete="new-password"
            minlength="12"
            required
          />
          <PasswordStrength :password="newPw" />
        </div>
        <div v-if="pwError" class="fh-notice" data-tone="error">{{ pwError }}</div>
        <button class="fh-btn" :disabled="pwSubmitting">
          {{ $t('account.change_password_submit') }}
        </button>
      </form>
    </section>

    <!-- 2FA -->
    <section id="2fa" class="account-section">
      <h2 class="account-h2">{{ $t('account.section_2fa') }}</h2>
      <div class="twofa-state">
        <div v-if="totpStatus?.enabled">
          <p class="twofa-on">
            <span class="dot" data-on />
            {{ $t('account.twofa_on') }}
          </p>
          <p class="fh-field-help">
            {{
              $t('account.twofa_recovery_remaining', {
                n: totpStatus.recovery_codes_remaining,
              })
            }}
          </p>
          <RouterLink to="/account/2fa" class="fh-btn-ghost fh-btn">
            {{ $t('account.twofa_manage_cta') }} <span aria-hidden="true">→</span>
          </RouterLink>
        </div>
        <div v-else>
          <p class="twofa-off">
            <span class="dot" />
            {{ $t('account.twofa_off') }}
          </p>
          <RouterLink to="/account/2fa" class="fh-btn">
            {{ $t('account.twofa_setup_cta') }} <span aria-hidden="true">→</span>
          </RouterLink>
        </div>
      </div>
    </section>

    <!-- Sessions -->
    <section id="sessions" class="account-section">
      <h2 class="account-h2">{{ $t('account.section_sessions') }}</h2>
      <ul class="sessions">
        <SessionRow
          v-for="s in sessions"
          :key="s.id"
          :session="s"
          @revoke="revokeSession"
        />
      </ul>
    </section>

    <!-- API tokens -->
    <section id="api-tokens" class="account-section">
      <ApiTokenPanel />
    </section>

    <!-- Notification preferences -->
    <section id="notifications" class="account-section">
      <NotificationPreferences />
    </section>

    <!-- WebAuthn / passkeys -->
    <section id="webauthn" class="account-section">
      <WebAuthnPanel />
    </section>

    <!-- OIDC connect — child component renders its own <section>; wrap so
         the quick-nav has a stable anchor without touching the panel. -->
    <section id="oidc" class="account-anchor">
      <OIDCConnectPanel />
    </section>
    </div>

    <aside class="account-quicknav-rail">
      <SectionQuickNav
        :sections="sections"
        :active="active"
        :ariaLabel="t('account.quicknav.aria')"
        @jump="jumpTo"
      />
    </aside>
  </div>
</template>

<style scoped>
.account-layout {
  display: grid;
  grid-template-columns: minmax(0, var(--fh-max-width-prose)) 12rem;
  gap: var(--fh-space-6);
  align-items: start;
  max-width: 56rem;
  margin: 0 auto;
}

.account-prose {
  /* fh-prose centers itself by default; inside the grid it sits in column 1. */
  margin: 0;
}

.account-quicknav-rail {
  position: sticky;
  top: calc(var(--fh-app-header-height) + var(--fh-space-3));
  /* Lift the rail to align with the first heading instead of the page eyebrow. */
  padding-top: var(--fh-space-5);
}

@media (max-width: 920px) {
  .account-layout {
    grid-template-columns: minmax(0, var(--fh-max-width-prose));
    max-width: var(--fh-max-width-prose);
  }
  .account-quicknav-rail {
    display: none;
  }
}

.email-line {
  display: inline-flex;
  align-items: baseline;
  gap: var(--fh-space-3);
  color: var(--fh-subtle);
}

.role-pill {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--fh-accent);
  border: 1px solid var(--fh-accent);
  padding: 1px 6px;
}

.account-section,
.account-anchor {
  /* Keep the heading clear of the sticky AppHeader after click-scroll. */
  scroll-margin-top: calc(var(--fh-app-header-height) + var(--fh-space-3));
}

.account-section {
  margin: var(--fh-space-5) 0;
  padding-top: var(--fh-space-4);
  border-top: 1px solid var(--fh-hairline);
}

.account-section:first-of-type {
  border-top: none;
}

.account-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
  letter-spacing: -0.01em;
  margin: 0 0 var(--fh-space-3);
  color: var(--fh-ink);
}

.display-name-actions {
  display: flex;
  gap: var(--fh-space-3);
  margin-top: var(--fh-space-2);
  align-items: baseline;
}

.locale-pick {
  display: inline-flex;
  gap: var(--fh-space-3);
  margin-top: var(--fh-space-1);
}

.locale-opt {
  background: none;
  border: 1px solid var(--fh-hairline-strong);
  font: inherit;
  color: var(--fh-ink);
  padding: var(--fh-space-1) var(--fh-space-3);
  cursor: pointer;
  border-radius: var(--fh-radius-sm);
  transition: all var(--fh-duration-fast) var(--fh-easing);
}

.locale-opt.active {
  background: var(--fh-ink);
  color: var(--fh-paper);
  border-color: var(--fh-ink);
}

.twofa-state .dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--fh-subtle-soft);
  margin-right: var(--fh-space-2);
  vertical-align: middle;
}

.twofa-state .dot[data-on] {
  background: var(--fh-success);
}

.twofa-on,
.twofa-off {
  margin-bottom: var(--fh-space-3);
}

.sessions {
  list-style: none;
  padding: 0;
  margin: 0;
}
</style>
