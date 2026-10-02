<!-- Reveal a secret (v2.24.0): the one place the SPA shows a secret's content.

     Nothing is revealed until the reader clicks: a page load costs nothing,
     because mail gateways open links before people do. The content is kept in
     this component's state only - never in a store, the URL or storage - and is
     rendered as TEXT (interpolation, never v-html). When the view was the last
     one the warning says so, because leaving the page then loses it. -->
<template>
  <section class="secret-reveal" data-testid="secret-reveal">
    <template v-if="!result">
      <p
        v-if="viewsLeft === 1"
        class="fh-notice"
        data-tone="accent"
        data-testid="last-view-warning"
      >
        {{ t('secrets.reveal.last_view_warning') }}
      </p>
      <form class="reveal-form" @submit.prevent="onReveal">
        <!-- An answer to the reader's own request may need the passphrase they
             set when asking - a separate layer from the sender's. -->
        <label v-if="requiresRequestPassphrase" class="fh-field">
          <span class="fh-field-label">{{ t('secrets.reveal.request_passphrase_label') }}</span>
          <input
            v-model="requestPassphrase"
            class="fh-field-input fh-field-mono"
            type="password"
            autocomplete="off"
            data-testid="reveal-request-passphrase"
          />
          <span class="fh-field-help">{{ t('secrets.reveal.request_passphrase_help') }}</span>
        </label>
        <label v-if="requiresPassphrase" class="fh-field">
          <span class="fh-field-label">{{ t('secrets.reveal.passphrase_label') }}</span>
          <input
            v-model="passphrase"
            class="fh-field-input fh-field-mono"
            type="password"
            autocomplete="off"
            data-testid="reveal-passphrase"
          />
          <span class="fh-field-help">{{
            attemptsLeft != null
              ? t('secrets.reveal.passphrase_help_burn', { n: attemptsLeft }, attemptsLeft)
              : t('secrets.reveal.passphrase_help')
          }}</span>
        </label>
        <div v-if="error" class="fh-notice" role="alert" data-tone="error">{{ error }}</div>
        <button
          class="fh-btn"
          type="submit"
          :disabled="
            busy ||
            (requiresPassphrase && !passphrase) ||
            (requiresRequestPassphrase && !requestPassphrase)
          "
          data-testid="reveal-button"
        >
          {{ busy ? t('common.loading') : t('secrets.reveal.button') }}
        </button>
      </form>
    </template>

    <template v-else>
      <div class="secret-box">
        <pre v-if="shown" class="secret-content fh-mono" data-testid="secret-content">{{
          result.content
        }}</pre>
        <p v-else class="secret-hidden fh-mono">{{ t('secrets.reveal.hidden') }}</p>
        <div class="secret-actions">
          <button type="button" class="fh-btn" data-testid="secret-copy" @click="copy">
            {{ copied ? t('secrets.reveal.copied') : t('secrets.reveal.copy') }}
          </button>
          <button type="button" class="fh-btn-text" @click="shown = !shown">
            {{ shown ? t('secrets.reveal.hide') : t('secrets.reveal.show') }}
          </button>
        </div>
      </div>
      <p
        v-if="result.ended || result.views_left === 0"
        class="fh-notice"
        data-tone="accent"
        data-testid="reveal-gone"
      >
        {{ result.ended ? t('secrets.reveal.destroyed') : t('secrets.reveal.no_views_left') }}
      </p>
      <p v-else-if="result.views_left !== null" class="fh-field-help">
        {{ t('secrets.reveal.views_left', { n: result.views_left }, result.views_left) }}
      </p>
    </template>
  </section>
</template>

<script setup lang="ts">
  import { onBeforeUnmount, ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { asEnvelope } from '@/api/client'
  import { useApiError } from '@/composables/useApiError'
  import type { RevealSecretResponse } from '@/types/api'

  const props = defineProps<{
    requiresPassphrase: boolean
    viewsLeft: number | null
    /** Wrong passphrases left before it burns for this reader (burn mode). */
    attemptsLeft?: number | null
    /** The reader's own request passphrase is needed too (an answer to their request). */
    requiresRequestPassphrase?: boolean
    reveal: (
      passphrase: string | null,
      requestPassphrase: string | null,
    ) => Promise<RevealSecretResponse>
  }>()
  const emit = defineEmits<{
    revealed: [result: RevealSecretResponse]
    /** A failure that changes what the page should show (gone, locked...). */
    failed: [code: string]
  }>()

  const { t } = useI18n()
  const { describe } = useApiError()

  const passphrase = ref('')
  const requestPassphrase = ref('')
  const busy = ref(false)
  const error = ref<string | null>(null)
  const result = ref<RevealSecretResponse | null>(null)
  const shown = ref(true)
  const copied = ref(false)
  let copiedTimer: number | null = null

  // Codes after which there is nothing more to try on this page.
  const TERMINAL = new Set([
    'SECRET_ENDED',
    'SECRET_VIEWS_EXHAUSTED',
    'SECRET_RECIPIENT_BURNED',
    'SECRET_LINK_REVOKED',
    'SECRET_LOCKED',
    'SECRET_NOT_A_RECIPIENT',
  ])

  async function onReveal() {
    error.value = null
    busy.value = true
    try {
      const data = await props.reveal(
        props.requiresPassphrase ? passphrase.value : null,
        props.requiresRequestPassphrase ? requestPassphrase.value : null,
      )
      result.value = data
      passphrase.value = ''
      requestPassphrase.value = ''
      emit('revealed', data)
    } catch (err) {
      error.value = describe(err)
      const code = asEnvelope(err)?.code
      if (code) emit('failed', code)
      if (code && TERMINAL.has(code)) {
        passphrase.value = ''
        requestPassphrase.value = ''
      }
    } finally {
      busy.value = false
    }
  }

  async function copy() {
    if (!result.value) return
    try {
      await navigator.clipboard.writeText(result.value.content)
      copied.value = true
      if (copiedTimer) window.clearTimeout(copiedTimer)
      copiedTimer = window.setTimeout(() => (copied.value = false), 1600)
    } catch {
      /* clipboard blocked - the text is on screen to select */
    }
  }

  onBeforeUnmount(() => {
    if (copiedTimer) window.clearTimeout(copiedTimer)
    result.value = null
  })
</script>

<style scoped>
  .secret-reveal {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-3);
  }

  .reveal-form {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-3);
    max-width: 420px;
  }

  .reveal-form .fh-btn {
    align-self: flex-start;
  }

  .secret-box {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-2);
    padding: var(--fh-space-4);
    background: var(--fh-paper-raised);
    border: var(--fh-border-strong);
    border-radius: var(--fh-radius-sm);
  }

  .secret-content {
    margin: 0;
    padding: var(--fh-space-3);
    background: var(--fh-paper);
    border: var(--fh-border);
    border-radius: var(--fh-radius-sm);
    font-size: var(--fh-text-mono-md);
    white-space: pre-wrap;
    word-break: break-all;
    user-select: all;
  }

  .secret-hidden {
    margin: 0;
    color: var(--fh-subtle);
    letter-spacing: 0.3em;
  }

  .secret-actions {
    display: flex;
    gap: var(--fh-space-3);
    align-items: center;
  }
</style>
