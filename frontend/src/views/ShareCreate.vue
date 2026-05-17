<script setup lang="ts">
/* Compose a new share: subject + message + recipient + expiry + files.
 *
 * Flow:
 *   1. user fills metadata + drops files
 *   2. clicks "Send" — POST /api/shares creates the share
 *   3. uploads start, routed to that share_id (direct + TUS)
 *   4. when all files done, navigate to /share/{id} for authoritative view
 *
 * The form locks while uploads are in flight so users don't accidentally
 * mash Send twice. */
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { createShare } from '@/api/shares'
import ExpiryPicker from '@/components/ExpiryPicker.vue'
import FileUploadArea from '@/components/FileUploadArea.vue'
import RecipientPicker from '@/components/RecipientPicker.vue'
import { useApiError } from '@/composables/useApiError'
import { useUpload } from '@/composables/useUpload'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import type {
  InlinePublicLinkResult,
  PublicLinkOnCreate,
  ShareKind,
  ShareRecipientsRequest,
} from '@/types/api'

const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()
const { t } = useI18n()
const { describe } = useApiError()

// Outbound = employee/admin → client; Inbound = client → employee.
// Default per role: employees/admins go outbound; clients go inbound.
const kind = computed<ShareKind>(() =>
  auth.user?.role === 'client' ? 'inbound' : 'outbound',
)

const subject = ref('')
const message = ref('')
const recipients = ref<ShareRecipientsRequest>({ user_ids: [], group_ids: [] })
// null = user picked the "Never" preset (v1.1.4 — share never auto-deletes).
// Initial state is undefined so the picker's auto-emit on mount fills it
// with the default 7-day preset; from then on the picker always emits a
// concrete value (string OR null).
const expiresAtLocal = ref<string | null | undefined>(undefined)
// Per-share opt-out for the `share_created` notification + email fan-out.
// Initial state mirrors the admin-controlled kv (surfaced via /me) so the
// admin can decide whether senders see this on or off by default.
const notifyRecipients = ref(
  auth.user?.share_notify_recipients_default ?? true,
)

const shareId = ref<string | null>(null)
const upload = useUpload(shareId)
const submitting = ref(false)
const errorMsg = ref<string | null>(null)

// --- Inline public link --------------------------------------------------

const canCreatePublicLink = computed(
  () => auth.user?.can_create_public_link !== false,
)
const includePublicLink = ref(false)
const plPassword = ref('')
const plDownloadLimit = ref<number | null>(null)
const plNotifyOnDownload = ref(false)
// v1.1.0 per-share download limit (independent of public link).
// `null` means unlimited; positive integer = cap shared across all
// recipients + sender + admins.
const shareDownloadLimit = ref<number | null>(null)
const plResult = ref<InlinePublicLinkResult | null>(null)
const plCopied = ref(false)

async function copyPublicLink() {
  if (!plResult.value) return
  try {
    await navigator.clipboard.writeText(plResult.value.url)
    plCopied.value = true
    setTimeout(() => (plCopied.value = false), 1600)
  } catch {
    /* clipboard blocked */
  }
}

const canSubmit = computed(() => {
  const hasRecipients =
    recipients.value.user_ids.length > 0 ||
    recipients.value.group_ids.length > 0
  // Public-link-only shares (no directed recipient) are valid as long
  // as the user has the toggle on AND policy lets them create one.
  const hasPublicLink = includePublicLink.value && canCreatePublicLink.value
  if (!hasRecipients && !hasPublicLink) return false
  // Picker emits a value on mount (default 7d preset), so by the time
  // the user can click submit, expiresAtLocal is either a string (some
  // datetime) OR null (Never). undefined = picker hasn't initialized.
  if (expiresAtLocal.value === undefined) return false
  if (upload.items.value.length === 0) return false
  if (submitting.value) return false
  if (upload.isActive.value) return false
  return true
})

// 'finalizing' counts as "done enough" for navigation: the file is
// already on tusd's disk and the server-side post-finish hook is in
// flight; the next view (/share/{id}) re-fetches authoritative state
// on mount, so a brief flash we'd never see is fine. Without this,
// the TUS path raced because upload-success sets 'finalizing' then
// flips to 'done' on a 800ms timer in useUpload.ts —
// the check fires before that timer.
const allUploadsDone = computed(() =>
  upload.items.value.length > 0 &&
  upload.items.value.every((i) => i.state === 'done' || i.state === 'finalizing'),
)

function localIsoToUtcIso(local: string): string {
  // ExpiryPicker emits "YYYY-MM-DDTHH:mm:ss" in local time; Date()
  // parses that as local, and toISOString gives UTC in Z form. The
  // backend service strips any tzinfo and stores naive UTC.
  return new Date(local).toISOString()
}

async function onSubmit() {
  if (!canSubmit.value) return
  errorMsg.value = null
  submitting.value = true
  try {
    let publicLinkPayload: PublicLinkOnCreate | null = null
    if (includePublicLink.value && canCreatePublicLink.value) {
      publicLinkPayload = {
        password: plPassword.value || null,
        download_limit: plDownloadLimit.value || null,
        notify_on_download: plNotifyOnDownload.value,
      }
    }

    const { data } = await createShare({
      kind: kind.value,
      recipients: recipients.value,
      // null = "Never expires" (user picked the Never preset); else
      // local→UTC convert the picker's local ISO string.
      expires_at: expiresAtLocal.value === null
        ? null
        : localIsoToUtcIso(expiresAtLocal.value as string),
      subject: subject.value || null,
      message: message.value || null,
      public_link: publicLinkPayload,
      notify_recipients: notifyRecipients.value,
      download_limit: shareDownloadLimit.value || null,
    })
    shareId.value = data.id
    if (data.public_link) {
      plResult.value = data.public_link
    }
    submitting.value = false
    // Now route uploads through the share.
    await upload.start()
    if (allUploadsDone.value) {
      ui.pushToast(t('share_create.toast_done'), 'success')
      // If a public link was returned, hold on the page so the user can
      // copy it before routing to /share/{id}. Otherwise jump.
      if (!plResult.value) {
        await router.push({ name: 'share-detail', params: { id: data.id } })
      }
    } else {
      const errCount = upload.items.value.filter((i) => i.state === 'error').length
      ui.pushToast(t('share_create.toast_partial', { n: errCount }), 'warn')
    }
  } catch (err) {
    errorMsg.value = describe(err)
    submitting.value = false
  }
}

function dismissPlResult() {
  if (!shareId.value) return
  router.push({ name: 'share-detail', params: { id: shareId.value } })
}
</script>

<template>
  <div class="fh-page" data-density="operator">
    <span class="fh-eyebrow">{{ t('share_create.eyebrow') }}</span>
    <h1 class="fh-display-md">{{ t('share_create.title') }}</h1>
    <p class="fh-field-help intro">{{ t(`share_create.intro.${kind}`) }}</p>

    <hr class="fh-rule" />

    <form class="composer" @submit.prevent="onSubmit">
      <FileUploadArea
        :items="upload.items.value"
        :disabled="submitting"
        @add="upload.add"
        @remove="upload.remove"
        @retry="upload.retry"
      />

      <hr class="fh-rule" />

      <div class="grid">
        <div class="col">
          <label class="fh-field">
            <span class="fh-field-label">{{ t('share_create.subject_label') }}</span>
            <input
              v-model.trim="subject"
              class="fh-field-input"
              type="text"
              maxlength="255"
              :placeholder="t('share_create.subject_placeholder')"
              :disabled="submitting || upload.isActive.value"
            />
          </label>

          <label class="fh-field">
            <span class="fh-field-label">{{ t('share_create.message_label') }}</span>
            <textarea
              v-model.trim="message"
              class="fh-field-input message-input"
              maxlength="4000"
              :placeholder="t('share_create.message_placeholder')"
              :disabled="submitting || upload.isActive.value"
              rows="4"
            />
          </label>
        </div>

        <div class="col">
          <RecipientPicker
            v-model="recipients"
            :disabled="submitting || upload.isActive.value"
          />
          <p
            v-if="canCreatePublicLink"
            class="fh-field-help recipients-hint"
          >
            {{ t('share_create.recipients_or_public_link_hint') }}
          </p>
          <ExpiryPicker
            v-model="expiresAtLocal"
            :disabled="submitting || upload.isActive.value"
          />
          <label class="fh-field share-limit-field">
            <span class="fh-field-label">{{ t('share_create.download_limit_label') }}</span>
            <input
              v-model.number="shareDownloadLimit"
              class="fh-field-input fh-field-mono"
              type="number"
              min="1"
              max="100000"
              :placeholder="t('share_create.download_limit_placeholder')"
              :disabled="submitting || upload.isActive.value"
            />
            <span class="fh-field-help">{{ t('share_create.download_limit_help') }}</span>
          </label>
        </div>
      </div>

      <section class="notify-recipients-section">
        <hr class="fh-rule" />
        <label class="public-link-toggle">
          <input
            type="checkbox"
            v-model="notifyRecipients"
            :disabled="submitting || upload.isActive.value"
          />
          <span>
            <span class="toggle-name">{{ t('share_create.notify_recipients_label') }}</span>
            <span class="toggle-help">{{ t('share_create.notify_recipients_help') }}</span>
          </span>
        </label>
      </section>

      <section v-if="canCreatePublicLink" class="public-link-section">
        <hr class="fh-rule" />
        <label class="public-link-toggle">
          <input
            type="checkbox"
            v-model="includePublicLink"
            :disabled="submitting || upload.isActive.value"
          />
          <span>
            <span class="toggle-name">{{ t('share_create.public_link.toggle_label') }}</span>
            <span class="toggle-help">{{ t('share_create.public_link.toggle_help') }}</span>
          </span>
        </label>

        <div v-if="includePublicLink" class="public-link-fields">
          <label class="fh-field">
            <span class="fh-field-label">{{ t('share_create.public_link.password_label') }}</span>
            <input
              v-model="plPassword"
              type="password"
              autocomplete="off"
              class="fh-field-input fh-field-mono"
              :placeholder="t('share_create.public_link.password_placeholder')"
              :disabled="submitting || upload.isActive.value"
            />
            <span class="fh-field-help">{{ t('share_create.public_link.password_help') }}</span>
          </label>

          <label class="fh-field">
            <span class="fh-field-label">{{ t('share_create.public_link.download_limit_label') }}</span>
            <input
              v-model.number="plDownloadLimit"
              type="number"
              min="1"
              max="100000"
              class="fh-field-input fh-field-mono"
              :placeholder="t('share_create.public_link.download_limit_placeholder')"
              :disabled="submitting || upload.isActive.value"
            />
            <span class="fh-field-help">{{ t('share_create.public_link.download_limit_help') }}</span>
          </label>

          <label class="public-link-toggle compact">
            <input
              type="checkbox"
              v-model="plNotifyOnDownload"
              :disabled="submitting || upload.isActive.value"
            />
            <span>{{ t('share_create.public_link.notify_label') }}</span>
          </label>
        </div>
      </section>

      <div
        v-if="plResult"
        class="fh-rise plaintext-box"
      >
        <div class="plaintext-eyebrow">{{ t('share_create.public_link.result_eyebrow') }}</div>
        <div class="plaintext-warning">{{ t('share_create.public_link.result_warning') }}</div>
        <pre class="plaintext-token fh-mono">{{ plResult.url }}</pre>
        <div class="plaintext-actions">
          <button type="button" class="fh-btn-text" @click="copyPublicLink">
            {{ plCopied ? t('api_tokens.copied') : t('api_tokens.copy') }}
          </button>
          <button type="button" class="fh-btn-text" @click="dismissPlResult">
            {{ t('share_create.public_link.continue') }}
          </button>
        </div>
      </div>

      <div v-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

      <div v-if="!plResult" class="actions">
        <button class="fh-btn-text" type="button" @click="router.back()">
          {{ t('common.cancel') }}
        </button>
        <button class="fh-btn" type="submit" :disabled="!canSubmit">
          {{
            submitting || upload.isActive.value
              ? t('share_create.sending')
              : t('share_create.send')
          }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.intro {
  margin-top: 0;
  max-width: 60ch;
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
}

.message-input {
  resize: vertical;
  min-height: 6rem;
  font-family: inherit;
  line-height: 1.5;
  border-bottom: var(--fh-border-strong);
  border-left: none;
  border-right: none;
  border-top: none;
}

.actions {
  display: flex;
  gap: var(--fh-space-4);
  align-items: center;
  justify-content: flex-end;
  padding-top: var(--fh-space-3);
}

.public-link-section {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-3);
}

.public-link-toggle {
  display: flex;
  gap: var(--fh-space-2);
  align-items: flex-start;
  cursor: pointer;
}

.public-link-toggle.compact > span {
  display: inline;
}

.public-link-toggle > span {
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

.public-link-fields {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
  padding-left: var(--fh-space-4);
  border-left: 2px solid var(--fh-rule);
}

.plaintext-box {
  padding: var(--fh-space-4);
  background: var(--fh-accent-soft);
  border: var(--fh-border);
  border-left: 2px solid var(--fh-accent);
  border-radius: var(--fh-radius-sm);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.plaintext-eyebrow {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--fh-subtle);
}

.plaintext-warning {
  font-size: var(--fh-text-body-sm);
}

.plaintext-token {
  background: var(--fh-paper);
  padding: var(--fh-space-3);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  font-size: var(--fh-text-mono-md);
  word-break: break-all;
  white-space: pre-wrap;
  margin: 0;
  user-select: all;
}

.plaintext-actions {
  display: flex;
  gap: var(--fh-space-3);
}

@media (max-width: 720px) {
  .grid {
    grid-template-columns: 1fr;
    gap: var(--fh-space-3);
  }
}
</style>
