<script setup lang="ts">
  /* /secrets/requests/new - ask someone for a secret (v2.24.0).
   *
   * Who may be asked is who could be SENT a secret (a client asks only the
   * employees they are connected to - never a group, an address or a link).
   * The first answer closes the request and arrives as a secret only the
   * requester can open, within the limits set here. `canSubmit` is DERIVED
   * from the visible `blockers` list (the ShareCreate rule). */
  import { computed, ref } from 'vue'
  import { useI18n } from 'vue-i18n'
  import { useRouter } from 'vue-router'

  import { createSecretRequest } from '@/api/secretRequests'
  import ExpiryPicker from '@/components/ExpiryPicker.vue'
  import RecipientPicker from '@/components/RecipientPicker.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useAuthStore } from '@/stores/auth'
  import { useUiStore } from '@/stores/ui'
  import type { SecretRecipientsRequest, SecretRequestResponse } from '@/types/api'
  import { siteLocalIsoToEpochMs, siteLocalIsoToUtcIso } from '@/utils/datetime'

  const router = useRouter()
  const auth = useAuthStore()
  const ui = useUiStore()
  const { t } = useI18n()
  const { describe } = useApiError()

  const MIN_PASSPHRASE = 8
  const DAY_MS = 24 * 60 * 60 * 1000
  const HOUR_S = 3600
  const DAY_S = 24 * HOUR_S

  const isClient = computed(() => auth.user?.role === 'client')
  const isAdmin = computed(() => auth.user?.role === 'admin')
  const canExternal = computed(() => auth.user?.can_send_secrets_external === true)
  const limits = computed(
    () =>
      auth.user?.secret_limits ?? {
        max_views: 100,
        max_expiry_days: 90,
        max_lifetime_days: 90,
        passphrase_failure_mode: 'lock',
        passphrase_max_failures: 10,
      },
  )

  const label = ref('')
  const note = ref('')
  const recipients = ref<SecretRecipientsRequest>({ user_ids: [], group_ids: [], emails: [] })
  const recipientPending = ref<{ text: string; noAccount: boolean }>({ text: '', noAccount: false })
  const createLink = ref(false)
  // undefined until the picker's mount emits its default (7 days).
  const openUntilLocal = ref<string | null | undefined>(undefined)

  const limitViews = ref(true)
  const maxViews = ref<number | null>(1)
  // Seconds the answer lives after it arrives; null = no lifetime limit.
  const lifetime = ref<number | null>(7 * DAY_S)

  const passphrase = ref('')
  const passphraseRepeat = ref('')

  const submitting = ref(false)
  const errorMsg = ref<string | null>(null)
  const created = ref<SecretRequestResponse | null>(null)
  const copied = ref(false)

  /** "Open for" presets the admin's ceiling allows - never "no expiry". */
  const presets = computed(() => {
    const days: Record<string, number> = { '1d': 1, '7d': 7, '30d': 30, '90d': 90 }
    return Object.keys(days).filter((k) => days[k] <= limits.value.max_expiry_days) as (
      '1d' | '7d' | '30d' | '90d'
    )[]
  })

  const lifetimeOptions = computed(() => {
    const opts: { value: number; key: string }[] = [
      { value: HOUR_S, key: 'secret_requests.create.lifetime.1h' },
      { value: DAY_S, key: 'secret_requests.create.lifetime.1d' },
      { value: 7 * DAY_S, key: 'secret_requests.create.lifetime.7d' },
      { value: 30 * DAY_S, key: 'secret_requests.create.lifetime.30d' },
      { value: 90 * DAY_S, key: 'secret_requests.create.lifetime.90d' },
    ]
    return opts.filter((o) => o.value <= limits.value.max_expiry_days * DAY_S)
  })

  const effectiveMaxViews = computed(() => (limitViews.value ? maxViews.value : null))

  const blockers = computed<string[]>(() => {
    const out: string[] = []
    if (!label.value.trim()) out.push(t('secret_requests.create.blockers.no_label'))
    const r = recipients.value
    const pending = recipientPending.value
    if (pending.text) {
      out.push(
        pending.noAccount
          ? t('secrets.create.blockers.no_account', { q: pending.text })
          : t('secrets.create.blockers.recipient_not_added', { q: pending.text }),
      )
    } else if (
      !r.user_ids.length &&
      !r.group_ids.length &&
      !r.emails.length &&
      !(createLink.value && canExternal.value)
    ) {
      out.push(
        canExternal.value
          ? t('secret_requests.create.blockers.no_target_or_link')
          : t('secret_requests.create.blockers.no_target'),
      )
    }
    if (openUntilLocal.value === undefined) out.push(t('secrets.create.blockers.no_expiry_yet'))
    if (typeof openUntilLocal.value === 'string') {
      const at = siteLocalIsoToEpochMs(openUntilLocal.value)
      if (at <= Date.now()) out.push(t('secrets.create.blockers.expiry_past'))
      else if (at > Date.now() + limits.value.max_expiry_days * DAY_MS + 60_000)
        out.push(
          t('secrets.create.blockers.expiry_too_far', { days: limits.value.max_expiry_days }),
        )
    }
    if (effectiveMaxViews.value === null && lifetime.value === null)
      out.push(t('secret_requests.create.blockers.no_limit'))
    if (limitViews.value) {
      const v = maxViews.value
      if (!v || v < 1 || !Number.isInteger(v)) out.push(t('secrets.create.blockers.views_invalid'))
      else if (v > limits.value.max_views)
        out.push(t('secrets.create.blockers.views_too_many', { max: limits.value.max_views }))
    }
    if (passphrase.value) {
      if (passphrase.value.length < MIN_PASSPHRASE)
        out.push(t('secrets.create.blockers.passphrase_short', { n: MIN_PASSPHRASE }))
      else if (passphrase.value !== passphraseRepeat.value)
        out.push(t('secrets.create.blockers.passphrase_mismatch'))
    }
    return out
  })

  const canSubmit = computed(() => blockers.value.length === 0 && !submitting.value)

  async function onSubmit() {
    if (!canSubmit.value || typeof openUntilLocal.value !== 'string') return
    submitting.value = true
    errorMsg.value = null
    try {
      const { data } = await createSecretRequest({
        label: label.value.trim(),
        note: note.value.trim() || null,
        expires_at: siteLocalIsoToUtcIso(openUntilLocal.value),
        answer_max_views: effectiveMaxViews.value,
        answer_expires_in_sec: lifetime.value,
        passphrase: passphrase.value || null,
        recipients: recipients.value,
        create_link: createLink.value && canExternal.value,
      })
      passphrase.value = ''
      passphraseRepeat.value = ''
      ui.pushToast(t('secret_requests.create.sent_toast'), 'success')
      if (data.link_url) created.value = data
      else router.push({ name: 'secret-request-detail', params: { id: data.id } })
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      submitting.value = false
    }
  }

  async function copyLink() {
    if (!created.value?.link_url) return
    try {
      await navigator.clipboard.writeText(created.value.link_url)
      copied.value = true
      window.setTimeout(() => (copied.value = false), 1600)
    } catch {
      /* clipboard blocked - the link is on screen to select */
    }
  }
</script>

<template>
  <div class="fh-page" data-density="operator">
    <span class="fh-eyebrow">{{ t('secrets.eyebrow') }}</span>
    <h1 class="fh-display-md">{{ t('secret_requests.create.title') }}</h1>
    <p class="fh-field-help intro">{{ t('secret_requests.create.intro') }}</p>

    <hr class="fh-rule" />

    <section v-if="created" class="sent-panel" data-testid="request-sent">
      <h2 class="section-h2">{{ t('secret_requests.create.sent_title') }}</h2>
      <p class="fh-field-help">{{ t('secret_requests.create.link_help') }}</p>
      <pre class="link fh-mono" data-testid="request-link">{{ created.link_url }}</pre>
      <div class="actions-row">
        <button type="button" class="fh-btn" @click="copyLink">
          {{ copied ? t('secrets.copied') : t('secrets.copy_link') }}
        </button>
        <RouterLink
          :to="{ name: 'secret-request-detail', params: { id: created.id } }"
          class="fh-btn-text"
        >
          {{ t('secret_requests.create.view_status') }} <span aria-hidden="true">→</span>
        </RouterLink>
      </div>
      <!-- eslint-disable-next-line vue/no-v-html -- server-rendered, deterministic QR SVG of our own link (no user input) -->
      <div v-if="created.link_qr_svg" class="qr" v-html="created.link_qr_svg" />
    </section>

    <form v-else class="composer" @submit.prevent="onSubmit">
      <div class="grid">
        <div class="col">
          <label class="fh-field">
            <span class="fh-field-label">{{ t('secret_requests.create.label_label') }}</span>
            <input
              v-model="label"
              class="fh-field-input"
              type="text"
              maxlength="200"
              :placeholder="t('secret_requests.create.label_placeholder')"
              :disabled="submitting"
              data-testid="request-label"
            />
          </label>
          <label class="fh-field">
            <span class="fh-field-label">{{ t('secret_requests.create.note_label') }}</span>
            <textarea
              v-model="note"
              class="fh-field-input"
              rows="3"
              maxlength="1000"
              :disabled="submitting"
              data-testid="request-note"
            />
            <span class="fh-field-help">{{ t('secret_requests.create.note_help') }}</span>
          </label>
        </div>

        <div class="col">
          <RecipientPicker
            v-model="recipients"
            purpose="request"
            :allow-groups="!isClient"
            :allow-external="canExternal"
            :is-admin="isAdmin"
            :disabled="submitting"
            @update:pending="recipientPending = $event"
          />
          <p v-if="isClient" class="fh-field-help">
            {{ t('secret_requests.create.client_hint') }}
          </p>
          <label v-if="canExternal" class="toggle">
            <input
              v-model="createLink"
              type="checkbox"
              :disabled="submitting"
              data-testid="request-create-link"
            />
            <span>
              <span class="toggle-name">{{ t('secret_requests.create.link_label') }}</span>
              <span class="toggle-help">{{ t('secret_requests.create.link_toggle_help') }}</span>
            </span>
          </label>
          <div class="open-until">
            <ExpiryPicker
              v-model="openUntilLocal"
              :label="t('secret_requests.create.open_until_label')"
              :presets="presets"
              :disabled="submitting"
            />
            <span class="fh-field-help">{{ t('secret_requests.create.open_until_help') }}</span>
          </div>
        </div>
      </div>

      <hr class="fh-rule" />

      <section class="limits">
        <h2 class="section-h2">{{ t('secret_requests.create.limits_title') }}</h2>
        <p class="fh-field-help">{{ t('secret_requests.create.limits_help') }}</p>
        <div class="grid">
          <div class="col">
            <label class="toggle">
              <input
                v-model="limitViews"
                type="checkbox"
                :disabled="submitting"
                data-testid="request-limit-views"
              />
              <span class="toggle-name">{{ t('secrets.create.limit_views') }}</span>
            </label>
            <label v-if="limitViews" class="fh-field">
              <span class="fh-field-label">{{ t('secrets.create.views_label') }}</span>
              <input
                v-model.number="maxViews"
                class="fh-field-input fh-field-mono views-input"
                type="number"
                min="1"
                :max="limits.max_views"
                :disabled="submitting"
                data-testid="request-max-views"
              />
            </label>
          </div>
          <label class="fh-field col">
            <span class="fh-field-label">{{ t('secret_requests.create.lifetime_label') }}</span>
            <select
              v-model="lifetime"
              class="fh-field-input lifetime-select"
              :disabled="submitting"
              data-testid="request-lifetime"
            >
              <option v-for="o in lifetimeOptions" :key="o.value" :value="o.value">
                {{ t(o.key) }}
              </option>
              <option :value="null">{{ t('secret_requests.create.lifetime.none') }}</option>
            </select>
            <span class="fh-field-help">{{ t('secret_requests.create.lifetime_help') }}</span>
          </label>
        </div>
      </section>

      <hr class="fh-rule" />

      <section class="protection">
        <h2 class="section-h2">{{ t('secret_requests.create.passphrase_title') }}</h2>
        <p class="fh-field-help">{{ t('secret_requests.create.passphrase_help') }}</p>
        <div class="grid">
          <label class="fh-field">
            <span class="fh-field-label">{{ t('secrets.create.passphrase_label') }}</span>
            <input
              v-model="passphrase"
              class="fh-field-input fh-field-mono"
              type="password"
              autocomplete="new-password"
              :disabled="submitting"
              data-testid="request-passphrase"
            />
          </label>
          <label v-if="passphrase" class="fh-field">
            <span class="fh-field-label">{{ t('secrets.create.passphrase_repeat') }}</span>
            <input
              v-model="passphraseRepeat"
              class="fh-field-input fh-field-mono"
              type="password"
              autocomplete="new-password"
              :disabled="submitting"
              data-testid="request-passphrase-repeat"
            />
          </label>
        </div>
      </section>

      <div v-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

      <div
        v-if="blockers.length && !submitting"
        id="request-create-blockers"
        class="blockers"
        aria-live="polite"
        data-testid="request-blockers"
      >
        <span class="fh-field-label">{{ t('secrets.create.blockers.title') }}</span>
        <ul>
          <li v-for="b in blockers" :key="b">{{ b }}</li>
        </ul>
      </div>

      <div class="actions">
        <button class="fh-btn-text" type="button" @click="router.back()">
          {{ t('common.cancel') }}
        </button>
        <button
          class="fh-btn"
          type="submit"
          :disabled="!canSubmit"
          :aria-describedby="blockers.length ? 'request-create-blockers' : undefined"
          data-testid="request-submit"
        >
          {{ submitting ? t('secret_requests.create.sending') : t('secret_requests.create.send') }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
  .intro {
    margin-top: 0;
    max-width: 64ch;
  }

  .composer {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-4);
  }

  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--fh-space-5);
  }

  .col {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-3);
  }

  .open-until {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-1);
  }

  .views-input {
    max-width: 10rem;
  }

  .lifetime-select {
    max-width: 16rem;
  }

  .section-h2 {
    font-family: var(--fh-font-display);
    font-size: 1.25rem;
    font-weight: 400;
    margin: 0 0 var(--fh-space-1);
  }

  .limits,
  .protection {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-2);
  }

  .toggle {
    display: flex;
    gap: var(--fh-space-2);
    align-items: flex-start;
    cursor: pointer;
  }

  .toggle > span {
    display: flex;
    flex-direction: column;
  }

  .toggle-name {
    font-weight: 500;
  }

  .toggle-help {
    font-size: var(--fh-text-body-sm);
    color: var(--fh-subtle);
  }

  .blockers {
    align-self: flex-end;
    max-width: 60ch;
    font-size: var(--fh-text-body-sm);
    color: var(--fh-ink-soft);
  }

  .blockers ul {
    margin: var(--fh-space-1) 0 0;
    padding-left: var(--fh-space-4);
  }

  .actions,
  .actions-row {
    display: flex;
    gap: var(--fh-space-4);
    align-items: center;
  }

  .actions {
    justify-content: flex-end;
    padding-top: var(--fh-space-3);
  }

  .sent-panel {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-3);
    max-width: 720px;
  }

  .link {
    margin: 0;
    padding: var(--fh-space-3);
    background: var(--fh-paper-raised);
    border: var(--fh-border);
    border-radius: var(--fh-radius-sm);
    word-break: break-all;
    white-space: pre-wrap;
    user-select: all;
  }

  .qr {
    width: 180px;
  }

  @media (max-width: 720px) {
    .grid {
      grid-template-columns: 1fr;
      gap: var(--fh-space-3);
    }
  }
</style>
