<script setup lang="ts">
  /* /secrets/new - send a secret (v2.24.0).
   *
   * The secret is a text, limited by views, by expiry, or both. `canSubmit` is
   * DERIVED from the visible `blockers` list (the ShareCreate rule): the button
   * can never be disabled for a reason the page does not show. A client sends
   * only to employees they are connected to, so the picker shows no groups and
   * offers no address or link to a client. */
  import { computed, ref, watch } from 'vue'
  import { useI18n } from 'vue-i18n'
  import { useRouter } from 'vue-router'

  import { createSecret } from '@/api/secrets'
  import ExpiryPicker from '@/components/ExpiryPicker.vue'
  import PasswordGenerator from '@/components/PasswordGenerator.vue'
  import RecipientPicker from '@/components/RecipientPicker.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useAuthStore } from '@/stores/auth'
  import { useUiStore } from '@/stores/ui'
  import type { SecretRecipientsRequest, SecretResponse, SecretViewScope } from '@/types/api'
  import { siteLocalIsoToEpochMs, siteLocalIsoToUtcIso } from '@/utils/datetime'

  const router = useRouter()
  const auth = useAuthStore()
  const ui = useUiStore()
  const { t } = useI18n()
  const { describe } = useApiError()

  const MAX_CONTENT = 10_000
  const MIN_PASSPHRASE = 8
  const DAY_MS = 24 * 60 * 60 * 1000

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
  const adminBurns = computed(() => limits.value.passphrase_failure_mode === 'burn')

  const content = ref('')
  const label = ref('')
  const recipients = ref<SecretRecipientsRequest>({ user_ids: [], group_ids: [], emails: [] })
  const recipientPending = ref<{ text: string; noAccount: boolean }>({ text: '', noAccount: false })
  const createLink = ref(false)

  const limitViews = ref(true)
  const maxViews = ref<number | null>(1)
  const viewScope = ref<SecretViewScope>('per_person')
  // undefined until the picker's mount emits its default (7 days); null = no
  // expiry; otherwise a site-timezone wall-clock string.
  const expiresAtLocal = ref<string | null | undefined>(undefined)

  const passphrase = ref('')
  const passphraseRepeat = ref('')
  const burnOnFailures = ref(false)
  const notifyOnView = ref(false)

  const submitting = ref(false)
  const errorMsg = ref<string | null>(null)
  const created = ref<SecretResponse | null>(null)
  const copied = ref(false)

  /** Expiry presets the admin's ceiling allows, plus "no expiry". */
  const presets = computed(() => {
    const days: Record<string, number> = { '1h': 1 / 24, '1d': 1, '7d': 7, '30d': 30, '90d': 90 }
    const ok = Object.keys(days).filter((k) => days[k] <= limits.value.max_expiry_days)
    return [...ok, 'never'] as ('1h' | '1d' | '7d' | '30d' | '90d' | 'never')[]
  })

  const hasGroup = computed(() => recipients.value.group_ids.length > 0)
  // Without a group, "each person" and "each recipient" are the same thing -
  // offer the choice only when it means something.
  const scopeOptions = computed<SecretViewScope[]>(() =>
    hasGroup.value ? ['per_person', 'per_recipient', 'total'] : ['per_person', 'total'],
  )
  watch(scopeOptions, (opts) => {
    if (!opts.includes(viewScope.value)) viewScope.value = 'per_person'
  })
  watch(limitViews, (on) => {
    if (on && maxViews.value === null) maxViews.value = 1
  })

  const effectiveMaxViews = computed(() => (limitViews.value ? maxViews.value : null))
  const noExpiry = computed(() => expiresAtLocal.value === null)

  const blockers = computed<string[]>(() => {
    const out: string[] = []
    if (!content.value) out.push(t('secrets.create.blockers.no_content'))
    else if (content.value.length > MAX_CONTENT) out.push(t('secrets.create.blockers.too_long'))
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
          ? t('secrets.create.blockers.no_recipient_or_link')
          : t('secrets.create.blockers.no_recipient'),
      )
    }
    if (expiresAtLocal.value === undefined) out.push(t('secrets.create.blockers.no_expiry_yet'))
    if (effectiveMaxViews.value === null && noExpiry.value) {
      out.push(t('secrets.create.blockers.no_limit'))
    }
    if (limitViews.value) {
      const v = maxViews.value
      if (!v || v < 1 || !Number.isInteger(v)) out.push(t('secrets.create.blockers.views_invalid'))
      else if (v > limits.value.max_views)
        out.push(t('secrets.create.blockers.views_too_many', { max: limits.value.max_views }))
    }
    if (typeof expiresAtLocal.value === 'string') {
      const at = siteLocalIsoToEpochMs(expiresAtLocal.value)
      if (at <= Date.now()) out.push(t('secrets.create.blockers.expiry_past'))
      else if (at > Date.now() + limits.value.max_expiry_days * DAY_MS + 60_000)
        out.push(
          t('secrets.create.blockers.expiry_too_far', { days: limits.value.max_expiry_days }),
        )
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

  function insertGenerated(value: string) {
    content.value = content.value ? `${content.value}\n${value}` : value
  }

  async function onSubmit() {
    if (!canSubmit.value) return
    submitting.value = true
    errorMsg.value = null
    try {
      const { data } = await createSecret({
        content: content.value,
        label: label.value.trim() || null,
        passphrase: passphrase.value || null,
        max_views: effectiveMaxViews.value,
        view_scope: viewScope.value,
        expires_at:
          typeof expiresAtLocal.value === 'string'
            ? siteLocalIsoToUtcIso(expiresAtLocal.value)
            : null,
        recipients: recipients.value,
        create_link: createLink.value && canExternal.value,
        notify_on_view: notifyOnView.value,
        burn_on_failures: burnOnFailures.value && !!passphrase.value,
      })
      // The text is not kept a moment longer than it has to be.
      content.value = ''
      passphrase.value = ''
      passphraseRepeat.value = ''
      ui.pushToast(t('secrets.create.sent_toast'), 'success')
      if (data.link_url) created.value = data
      else router.push({ name: 'secret-detail', params: { id: data.id } })
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
    <h1 class="fh-display-md">{{ t('secrets.create.title') }}</h1>
    <p class="fh-field-help intro">{{ t('secrets.create.intro') }}</p>

    <hr class="fh-rule" />

    <section v-if="created" class="sent-panel" data-testid="secret-sent">
      <h2 class="section-h2">{{ t('secrets.create.sent_title') }}</h2>
      <p class="fh-field-help">{{ t('secrets.create.link_help') }}</p>
      <pre class="link fh-mono" data-testid="secret-link">{{ created.link_url }}</pre>
      <div class="actions-row">
        <button type="button" class="fh-btn" @click="copyLink">
          {{ copied ? t('secrets.copied') : t('secrets.copy_link') }}
        </button>
        <RouterLink :to="{ name: 'secret-detail', params: { id: created.id } }" class="fh-btn-text">
          {{ t('secrets.create.view_status') }} <span aria-hidden="true">→</span>
        </RouterLink>
      </div>
      <!-- eslint-disable-next-line vue/no-v-html -- server-rendered, deterministic QR SVG of our own link (no user input) -->
      <div v-if="created.link_qr_svg" class="qr" v-html="created.link_qr_svg" />
    </section>

    <form v-else class="composer" @submit.prevent="onSubmit">
      <div class="grid">
        <div class="col">
          <label class="fh-field">
            <span class="fh-field-label">{{ t('secrets.create.content_label') }}</span>
            <textarea
              v-model="content"
              class="fh-field-input fh-field-mono content-input"
              rows="6"
              spellcheck="false"
              autocomplete="off"
              :maxlength="MAX_CONTENT"
              :disabled="submitting"
              data-testid="secret-content-input"
            />
            <span class="fh-field-help">{{ t('secrets.create.content_help') }}</span>
          </label>
          <PasswordGenerator :disabled="submitting" @use="insertGenerated" />

          <label class="fh-field">
            <span class="fh-field-label">{{ t('secrets.create.label_label') }}</span>
            <input
              v-model="label"
              class="fh-field-input"
              type="text"
              maxlength="200"
              :placeholder="t('secrets.create.label_placeholder')"
              :disabled="submitting"
            />
            <span class="fh-field-help">{{ t('secrets.create.label_help') }}</span>
          </label>
        </div>

        <div class="col">
          <RecipientPicker
            v-model="recipients"
            purpose="secret"
            :allow-groups="!isClient"
            :allow-external="canExternal"
            :is-admin="isAdmin"
            :disabled="submitting"
            @update:pending="recipientPending = $event"
          />
          <p v-if="isClient" class="fh-field-help">{{ t('secrets.create.client_hint') }}</p>

          <label v-if="canExternal" class="toggle">
            <input
              v-model="createLink"
              type="checkbox"
              :disabled="submitting"
              data-testid="create-link"
            />
            <span>
              <span class="toggle-name">{{ t('secrets.create.link_label') }}</span>
              <span class="toggle-help">{{ t('secrets.create.link_toggle_help') }}</span>
            </span>
          </label>
        </div>
      </div>

      <hr class="fh-rule" />

      <section class="limits">
        <h2 class="section-h2">{{ t('secrets.create.limits_title') }}</h2>
        <p class="fh-field-help">{{ t('secrets.create.limits_help') }}</p>
        <div class="grid">
          <div class="col">
            <label class="toggle">
              <input v-model="limitViews" type="checkbox" :disabled="submitting" />
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
                data-testid="max-views"
              />
            </label>
            <fieldset v-if="limitViews" class="scope" :disabled="submitting">
              <legend class="fh-field-label">{{ t('secrets.create.scope_label') }}</legend>
              <label v-for="opt in scopeOptions" :key="opt" class="scope-option">
                <input v-model="viewScope" type="radio" :value="opt" />
                <span>
                  <span class="toggle-name">{{ t(`secrets.scope.${opt}`) }}</span>
                  <span class="toggle-help">{{ t(`secrets.scope.${opt}_help`) }}</span>
                </span>
              </label>
            </fieldset>
          </div>
          <div class="col">
            <ExpiryPicker v-model="expiresAtLocal" :presets="presets" :disabled="submitting" />
            <p v-if="noExpiry && limits.max_lifetime_days > 0" class="fh-field-help">
              {{ t('secrets.create.lifetime_note', { days: limits.max_lifetime_days }) }}
            </p>
          </div>
        </div>
      </section>

      <hr class="fh-rule" />

      <section class="protection">
        <h2 class="section-h2">{{ t('secrets.create.passphrase_title') }}</h2>
        <p class="fh-field-help">{{ t('secrets.create.passphrase_help') }}</p>
        <div class="grid">
          <label class="fh-field">
            <span class="fh-field-label">{{ t('secrets.create.passphrase_label') }}</span>
            <input
              v-model="passphrase"
              class="fh-field-input fh-field-mono"
              type="password"
              autocomplete="new-password"
              :disabled="submitting"
              data-testid="passphrase"
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
              data-testid="passphrase-repeat"
            />
          </label>
        </div>
        <template v-if="passphrase">
          <p v-if="adminBurns" class="fh-field-help">
            {{ t('secrets.create.burn_by_admin', { n: limits.passphrase_max_failures }) }}
          </p>
          <label v-else class="toggle">
            <input v-model="burnOnFailures" type="checkbox" :disabled="submitting" />
            <span>
              <span class="toggle-name">{{
                t('secrets.create.burn_label', { n: limits.passphrase_max_failures })
              }}</span>
              <span class="toggle-help">{{ t('secrets.create.burn_help') }}</span>
            </span>
          </label>
        </template>
      </section>

      <hr class="fh-rule" />

      <label class="toggle">
        <input v-model="notifyOnView" type="checkbox" :disabled="submitting" />
        <span>
          <span class="toggle-name">{{ t('secrets.create.notify_label') }}</span>
          <span class="toggle-help">{{ t('secrets.create.notify_help') }}</span>
        </span>
      </label>

      <div v-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

      <div
        v-if="blockers.length && !submitting"
        id="secret-create-blockers"
        class="blockers"
        aria-live="polite"
        data-testid="submit-blockers"
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
          :aria-describedby="blockers.length ? 'secret-create-blockers' : undefined"
          data-testid="secret-submit"
        >
          {{ submitting ? t('secrets.create.sending') : t('secrets.create.send') }}
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

  .content-input {
    resize: vertical;
    min-height: 8rem;
    line-height: 1.5;
  }

  .views-input {
    max-width: 10rem;
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

  .toggle,
  .scope-option {
    display: flex;
    gap: var(--fh-space-2);
    align-items: flex-start;
    cursor: pointer;
  }

  .toggle > span,
  .scope-option > span {
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

  .scope {
    border: 1px solid var(--fh-rule);
    border-radius: var(--fh-radius-sm);
    padding: var(--fh-space-3);
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-2);
    margin: 0;
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
