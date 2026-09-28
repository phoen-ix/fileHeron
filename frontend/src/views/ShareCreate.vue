<script setup lang="ts">
  /* Compose a new share: subject + message + recipient + expiry + files.
   *
   * Flow:
   *   1. user fills metadata + drops files
   *   2. clicks "Send" - POST /api/shares creates the share
   *   3. uploads start, routed to that share_id (direct + TUS)
   *   4. when all files done, navigate to /share/{id} for authoritative view
   *
   * The form locks while uploads are in flight so users don't accidentally
   * mash Send twice. */
  import { computed, ref, watch } from 'vue'
  import { useI18n } from 'vue-i18n'
  import { useRouter } from 'vue-router'

  import { inviteUser } from '@/api/account'
  import { createShare, registerFilesAdded } from '@/api/shares'
  import ExpiryPicker from '@/components/ExpiryPicker.vue'
  import FileUploadArea from '@/components/FileUploadArea.vue'
  import RecipientPicker from '@/components/RecipientPicker.vue'
  import ShareUploadProgress from '@/components/ShareUploadProgress.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useUpload } from '@/composables/useUpload'
  import { useUploadLeaveGuard } from '@/composables/useUploadLeaveGuard'
  import { createSettledRegistrar } from '@/composables/settledRegistrar'
  import { siteLocalIsoToUtcIso } from '@/utils/datetime'
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
  const kind = computed<ShareKind>(() => (auth.user?.role === 'client' ? 'inbound' : 'outbound'))
  // Clients submit to the whole company - they don't pick recipients (the
  // server ignores any and resolves the audience by role/group at read time).
  const isClient = computed(() => auth.user?.role === 'client')

  const subject = ref('')
  const message = ref('')
  const recipients = ref<ShareRecipientsRequest>({ user_ids: [], group_ids: [], emails: [] })
  // Text typed into the recipient search that never became a recipient. It is
  // not sent anywhere; it blocks submit and is named in the blocker list, so a
  // typed-but-unpicked address can no longer leave the button grey for no
  // visible reason.
  // `noAccount`: the text is an address that cannot be added at all (no
  // account, and sending to such addresses is off) - "pick it from the list"
  // would be impossible advice, so the blocker says what IS possible.
  const recipientPending = ref<{ text: string; noAccount: boolean }>({
    text: '',
    noAccount: false,
  })
  const isAdmin = computed(() => auth.user?.role === 'admin')
  // null = user picked the "Never" preset (v1.1.4 - share never auto-deletes).
  // Initial state is undefined so the picker's auto-emit on mount fills it
  // with the default 7-day preset; from then on the picker always emits a
  // concrete value (string OR null).
  const expiresAtLocal = ref<string | null | undefined>(undefined)
  // A preset counts from when the files are READY (v2.23.0): its duration, or
  // null for a typed date / "never", which stay exact. A big upload used to eat
  // into "1 hour" and could outlive the share.
  const expiresRelativeSec = ref<number | null>(null)
  // Per-share opt-out for the `share_created` notification + email fan-out.
  // Initial state mirrors the admin-controlled kv (surfaced via /me) so the
  // admin can decide whether senders see this on or off by default.
  const notifyRecipients = ref(auth.user?.share_notify_recipients_default ?? true)

  const shareId = ref<string | null>(null)
  // v1.24.0: the created share landed in `pending_approval` (approval workflow).
  const createdPending = ref(false)
  const upload = useUpload(shareId)
  useUploadLeaveGuard(upload.isActive)
  const submitting = ref(false)
  const errorMsg = ref<string | null>(null)
  // 'compose' shows the form; 'progress' swaps in the dedicated upload screen
  // in place (no route change) so the live useUpload state survives.
  const phase = ref<'compose' | 'progress'>('compose')

  // --- Inline public link --------------------------------------------------

  const canCreatePublicLink = computed(() => auth.user?.can_create_public_link !== false)
  // Recipients with no account (v2.21.0): they get the public link by email,
  // so a share with any of them must carry one.
  const canShareExternal = computed(() => auth.user?.can_share_external === true)
  const externalEmails = computed(() => recipients.value.emails ?? [])
  const hasExternal = computed(() => externalEmails.value.length > 0)
  const offerInvite = computed(
    () => auth.user?.offer_invite_on_external === true && hasExternal.value,
  )
  // Addresses the sender ticked to ALSO invite as a client account. Unticked by
  // default: an account is a bigger step than a link, so it is asked, not assumed.
  const inviteChoices = ref<Record<string, boolean>>({})
  // The sender's choice whether addresses without an account are mailed the
  // link; unticked, they are recorded and the sender sends the link. Separate
  // from `notifyRecipients`, which is about account recipients.
  const emailExternalLink = ref(true)
  const includePublicLink = ref(false)
  watch(hasExternal, (v) => {
    if (v) includePublicLink.value = true
  })
  const plPassword = ref('')
  const plDownloadLimit = ref<number | null>(null)
  const plNotifyOnDownload = ref(false)
  // v1.1.0 per-share download limit (independent of public link).
  // `null` means unlimited; positive integer = cap shared across all
  // recipients + sender + admins.
  const shareDownloadLimit = ref<number | null>(null)
  const plResult = ref<InlinePublicLinkResult | null>(null)

  const errorCount = computed(() => upload.items.value.filter((i) => i.state === 'error').length)

  /** Why the form cannot be sent yet, in words. `canSubmit` is DERIVED from
   *  this list, so the button can never be disabled for a reason the list does
   *  not show - the defect this replaced was exactly that. */
  const blockers = computed<string[]>(() => {
    const out: string[] = []
    if (upload.items.value.length === 0) out.push(t('share_create.blockers.no_files'))
    if (!isClient.value) {
      const hasRecipients =
        recipients.value.user_ids.length > 0 ||
        recipients.value.group_ids.length > 0 ||
        hasExternal.value
      // Public-link-only shares (no directed recipient) are valid as long
      // as the user has the toggle on AND policy lets them create one.
      const hasPublicLink = includePublicLink.value && canCreatePublicLink.value
      // Clients always submit to the company, so they need neither a recipient
      // nor a public link - just files. Staff still require one of the two.
      const pending = recipientPending.value
      if (pending.text && pending.noAccount) {
        out.push(
          t(
            isAdmin.value
              ? 'share_create.blockers.no_account_admin'
              : 'share_create.blockers.no_account',
            { q: pending.text },
          ),
        )
      } else if (pending.text) {
        out.push(t('share_create.blockers.recipient_not_added', { q: pending.text }))
      } else if (!hasRecipients && !hasPublicLink) {
        out.push(
          canCreatePublicLink.value
            ? t('share_create.blockers.no_recipient_or_link')
            : t('share_create.blockers.no_recipient'),
        )
      }
    }
    // Picker emits a value on mount (default 7d preset), so by the time
    // the user can click submit, expiresAtLocal is either a string (some
    // datetime) OR null (Never). undefined = picker hasn't initialized.
    if (expiresAtLocal.value === undefined) out.push(t('share_create.blockers.no_expiry'))
    return out
  })

  const canSubmit = computed(
    () => blockers.value.length === 0 && !submitting.value && !upload.isActive.value,
  )

  // 'finalizing' counts as "done enough" for navigation: the file is
  // already on tusd's disk and the server-side post-finish hook is in
  // flight; the next view (/share/{id}) re-fetches authoritative state
  // on mount, so a brief flash we'd never see is fine. Without this,
  // the TUS path raced because upload-success sets 'finalizing' then
  // flips to 'done' on a 800ms timer in useUpload.ts -
  // the check fires before that timer.
  const allUploadsDone = computed(
    () =>
      upload.items.value.length > 0 &&
      upload.items.value.every((i) => i.state === 'done' || i.state === 'finalizing'),
  )

  async function onSubmit() {
    if (!canSubmit.value) return
    errorMsg.value = null
    submitting.value = true
    try {
      let publicLinkPayload: PublicLinkOnCreate | null = null
      if ((includePublicLink.value || hasExternal.value) && canCreatePublicLink.value) {
        publicLinkPayload = {
          password: plPassword.value || null,
          download_limit: plDownloadLimit.value || null,
          notify_on_download: plNotifyOnDownload.value,
        }
      }

      const { data } = await createShare({
        kind: kind.value,
        recipients: recipients.value,
        // null = "Never expires" (user picked the Never preset); else the
        // picker emits a site-tz wall-clock string - convert it to a UTC
        // instant interpreting it in the site tz (matches display).
        expires_at:
          expiresRelativeSec.value !== null || expiresAtLocal.value === null
            ? null
            : siteLocalIsoToUtcIso(expiresAtLocal.value as string),
        expires_in_sec: expiresRelativeSec.value,
        subject: subject.value || null,
        message: message.value || null,
        public_link: publicLinkPayload,
        notify_recipients: notifyRecipients.value,
        email_external_link: emailExternalLink.value,
        download_limit: shareDownloadLimit.value || null,
      })
      shareId.value = data.id
      createdPending.value = data.state === 'pending_approval'
      if (data.public_link) {
        plResult.value = data.public_link
      }
      submitting.value = false
      void inviteChosen()
      // Swap to the dedicated progress screen BEFORE uploads start, so the
      // list mounts while items are still 'queued' and the user watches each
      // one advance. Uploads keep running here because useUpload stays mounted.
      phase.value = 'progress'
      await upload.start()
      await registerSettled()
      if (allUploadsDone.value) {
        ui.pushToast(
          createdPending.value ? t('share_create.toast_pending') : t('share_create.toast_done'),
          'success',
        )
      } else {
        ui.pushToast(
          t('share_create.toast_partial', { n: errorCount.value }, errorCount.value),
          'warn',
        )
      }
    } catch (err) {
      // createShare failed before any swap - stay on the form with the error.
      errorMsg.value = describe(err)
      submitting.value = false
    }
  }

  // The share already reaches these addresses through the link; an invite is an
  // extra, so its failure (already invited, already has an account, not allowed)
  // is reported and never undoes the share. The invite route applies its own
  // rules - an employee may only invite clients, which is what is offered.
  async function inviteChosen() {
    if (!offerInvite.value) return
    const chosen = externalEmails.value.filter((e) => inviteChoices.value[e])
    for (const email of chosen) {
      try {
        await inviteUser({
          email,
          display_name_hint: email.split('@')[0] || email,
          target_role: 'client',
        })
        ui.pushToast(t('share_create.invite.sent', { email }), 'success')
      } catch (err) {
        ui.pushToast(
          t('share_create.invite.failed', { email, reason: describe(err) }),
          'warn',
          8000,
        )
      }
    }
  }

  // Tell the server which files have landed, so the recipient notification goes
  // out now with the right count rather than waiting for the fallback sweep. The
  // announcement is deferred until the files land - a share is empty at create
  // time, which is why every notification used to say "0 files" (audit #2).
  //
  // Every settled file exactly once. This ran once, after the first batch, so a
  // file that failed and then succeeded on Retry was never registered: the
  // recipients had been told "2 files", heard nothing about the third, and the
  // audit row listed two. The first call is the announcement; a later one is the
  // server's "files added" follow-up (register_files_added).
  const registrar = createSettledRegistrar({
    shareId: () => shareId.value,
    items: () => upload.items.value,
    register: (id, fileIds) =>
      registerFilesAdded(id, { notify: notifyRecipients.value, file_ids: fileIds }),
  })
  async function registerSettled() {
    try {
      await registrar.flush()
    } catch {
      // Best-effort: the announce sweep covers the first batch, and anything
      // left unsent is retried by the next flush.
    }
  }

  async function onRetry(uid: string) {
    await upload.retry(uid)
    await registerSettled()
  }

  function onViewShare() {
    if (!shareId.value) return
    router.push({ name: 'share-detail', params: { id: shareId.value } })
  }

  function onCreateAnother() {
    upload.reset()
    shareId.value = null
    plResult.value = null
    errorMsg.value = null
    subject.value = ''
    message.value = ''
    recipients.value = { user_ids: [], group_ids: [], emails: [] }
    recipientPending.value = { text: '', noAccount: false }
    emailExternalLink.value = true
    inviteChoices.value = {}
    // undefined → ExpiryPicker's mount auto-emit refills the 7-day default
    // when the form remounts (v-if, not v-show).
    expiresAtLocal.value = undefined
    notifyRecipients.value = auth.user?.share_notify_recipients_default ?? true
    shareDownloadLimit.value = null
    includePublicLink.value = false
    plPassword.value = ''
    plDownloadLimit.value = null
    plNotifyOnDownload.value = false
    phase.value = 'compose'
  }
</script>

<template>
  <div class="fh-page" data-density="operator">
    <span class="fh-eyebrow">{{ t('share_create.eyebrow') }}</span>
    <h1 class="fh-display-md">{{ t('share_create.title') }}</h1>
    <p class="fh-field-help intro">{{ t(`share_create.intro.${kind}`) }}</p>

    <hr class="fh-rule" />

    <form v-if="phase === 'compose'" class="composer" @submit.prevent="onSubmit">
      <FileUploadArea
        :items="upload.items.value"
        :disabled="submitting"
        @add="upload.add"
        @remove="upload.remove"
        @retry="onRetry"
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
          <!-- A client submission goes to the whole organisation, not to one
               person. That was documented nowhere the sender could see it, so
               say it here, where they are deciding what to upload. -->
          <p v-if="isClient" class="fh-notice" data-tone="info">
            {{ t('share_create.client_audience_notice') }}
          </p>
          <template v-if="!isClient">
            <RecipientPicker
              v-model="recipients"
              :disabled="submitting || upload.isActive.value"
              :allow-external="canShareExternal"
              :can-public-link="canCreatePublicLink"
              :is-admin="isAdmin"
              @update:pending="recipientPending = $event"
            />
            <p v-if="canCreatePublicLink" class="fh-field-help recipients-hint">
              {{ t('share_create.recipients_or_public_link_hint') }}
            </p>
          </template>
          <div v-else class="to-company">
            <span class="fh-field-label">{{ t('share_create.recipient_label') }}</span>
            <p class="fh-field-help">{{ t('share_create.to_company') }}</p>
          </div>
          <ExpiryPicker
            v-model="expiresAtLocal"
            v-model:relative="expiresRelativeSec"
            from-ready
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

      <section v-if="!isClient" class="notify-recipients-section">
        <hr class="fh-rule" />
        <label class="public-link-toggle">
          <input
            v-model="notifyRecipients"
            type="checkbox"
            :disabled="submitting || upload.isActive.value"
          />
          <span>
            <span class="toggle-name">{{ t('share_create.notify_recipients_label') }}</span>
            <span class="toggle-help">{{ t('share_create.notify_recipients_help') }}</span>
          </span>
        </label>
      </section>

      <section v-if="hasExternal" class="notify-recipients-section" data-testid="email-link">
        <hr class="fh-rule" />
        <label class="public-link-toggle">
          <input
            v-model="emailExternalLink"
            type="checkbox"
            :disabled="submitting || upload.isActive.value"
          />
          <span>
            <span class="toggle-name">{{ t('share_create.email_link.label') }}</span>
            <span class="toggle-help">{{ t('share_create.email_link.help') }}</span>
          </span>
        </label>
      </section>

      <section v-if="offerInvite" class="invite-section" data-testid="invite-offer">
        <hr class="fh-rule" />
        <span class="toggle-name">{{ t('share_create.invite.question') }}</span>
        <span class="toggle-help">{{ t('share_create.invite.help') }}</span>
        <label v-for="email in externalEmails" :key="email" class="public-link-toggle compact">
          <input
            v-model="inviteChoices[email]"
            type="checkbox"
            :disabled="submitting || upload.isActive.value"
          />
          <span>{{ t('share_create.invite.option', { email }) }}</span>
        </label>
      </section>

      <section v-if="canCreatePublicLink" class="public-link-section">
        <hr class="fh-rule" />
        <label class="public-link-toggle">
          <input
            v-model="includePublicLink"
            type="checkbox"
            :disabled="submitting || upload.isActive.value || hasExternal"
          />
          <span>
            <span class="toggle-name">{{ t('share_create.public_link.toggle_label') }}</span>
            <span class="toggle-help">{{
              hasExternal
                ? t('share_create.public_link.required_for_external')
                : t('share_create.public_link.toggle_help')
            }}</span>
          </span>
        </label>

        <div v-if="includePublicLink || hasExternal" class="public-link-fields">
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
            <span class="fh-field-help">{{
              hasExternal
                ? t('share_create.public_link.password_help_external')
                : t('share_create.public_link.password_help')
            }}</span>
          </label>

          <label class="fh-field">
            <span class="fh-field-label">{{
              t('share_create.public_link.download_limit_label')
            }}</span>
            <input
              v-model.number="plDownloadLimit"
              type="number"
              min="1"
              max="100000"
              class="fh-field-input fh-field-mono"
              :placeholder="t('share_create.public_link.download_limit_placeholder')"
              :disabled="submitting || upload.isActive.value"
            />
            <span class="fh-field-help">{{
              t('share_create.public_link.download_limit_help')
            }}</span>
          </label>

          <label class="public-link-toggle compact">
            <input
              v-model="plNotifyOnDownload"
              type="checkbox"
              :disabled="submitting || upload.isActive.value"
            />
            <span>{{ t('share_create.public_link.notify_label') }}</span>
          </label>
        </div>
      </section>

      <div v-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

      <div
        v-if="blockers.length && !submitting && !upload.isActive.value"
        id="share-create-blockers"
        class="blockers"
        aria-live="polite"
        data-testid="submit-blockers"
      >
        <span class="fh-field-label">{{ t('share_create.blockers.title') }}</span>
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
          :aria-describedby="blockers.length ? 'share-create-blockers' : undefined"
        >
          {{
            submitting || upload.isActive.value ? t('share_create.sending') : t('share_create.send')
          }}
        </button>
      </div>
    </form>

    <ShareUploadProgress
      v-else
      :items="upload.items.value"
      :public-link="plResult"
      :log="upload.log.value"
      :is-active="upload.isActive.value"
      :all-done="allUploadsDone"
      :error-count="errorCount"
      @retry="onRetry"
      @view-share="onViewShare"
      @create-another="onCreateAnother"
    />
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

  .invite-section {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-2);
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

  .public-link-fields {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-2);
    padding-left: var(--fh-space-4);
    border-left: 2px solid var(--fh-rule);
  }

  @media (max-width: 720px) {
    .grid {
      grid-template-columns: 1fr;
      gap: var(--fh-space-3);
    }
  }
</style>
