<!-- Answer a secret request (v2.24.0): the form a signed-in target and the
     anonymous /r page share. The text goes to the server once, on submit, and
     is cleared from the page the moment it is sent. `canSubmit` is DERIVED from
     the visible blockers (the ShareCreate rule). -->
<template>
  <form class="answer-form" data-testid="secret-answer-form" @submit.prevent="onSubmit">
    <p class="fh-field-help who">
      {{
        hasRequestPassphrase
          ? t('secret_requests.answer.only_requester_passphrase', { name: requesterLabel })
          : t('secret_requests.answer.only_requester', { name: requesterLabel })
      }}
    </p>
    <label class="fh-field">
      <span class="fh-field-label">{{ t('secret_requests.answer.content_label') }}</span>
      <textarea
        v-model="content"
        class="fh-field-input fh-field-mono content-input"
        rows="5"
        spellcheck="false"
        autocomplete="off"
        :maxlength="MAX_CONTENT"
        :disabled="busy"
        data-testid="answer-content"
      />
    </label>
    <PasswordGenerator :disabled="busy" @use="insertGenerated" />

    <details class="own-passphrase">
      <summary>{{ t('secret_requests.answer.passphrase_toggle') }}</summary>
      <p class="fh-field-help">{{ t('secret_requests.answer.passphrase_help') }}</p>
      <div class="pp-grid">
        <label class="fh-field">
          <span class="fh-field-label">{{ t('secrets.create.passphrase_label') }}</span>
          <input
            v-model="passphrase"
            class="fh-field-input fh-field-mono"
            type="password"
            autocomplete="new-password"
            :disabled="busy"
            data-testid="answer-passphrase"
          />
        </label>
        <label v-if="passphrase" class="fh-field">
          <span class="fh-field-label">{{ t('secrets.create.passphrase_repeat') }}</span>
          <input
            v-model="passphraseRepeat"
            class="fh-field-input fh-field-mono"
            type="password"
            autocomplete="new-password"
            :disabled="busy"
            data-testid="answer-passphrase-repeat"
          />
        </label>
      </div>
    </details>

    <div v-if="error" class="fh-notice" role="alert" data-tone="error">{{ error }}</div>
    <div v-if="blockers.length && !busy" class="blockers" aria-live="polite">
      <ul>
        <li v-for="b in blockers" :key="b">{{ b }}</li>
      </ul>
    </div>
    <button class="fh-btn" type="submit" :disabled="!canSubmit" data-testid="answer-submit">
      {{ busy ? t('secret_requests.answer.sending') : t('secret_requests.answer.send') }}
    </button>
  </form>
</template>

<script setup lang="ts">
  import { computed, ref } from 'vue'
  import { useI18n } from 'vue-i18n'

  import PasswordGenerator from '@/components/PasswordGenerator.vue'
  import { useApiError } from '@/composables/useApiError'

  const props = defineProps<{
    requesterName: string | null
    hasRequestPassphrase: boolean
    submit: (content: string, passphrase: string | null) => Promise<void>
  }>()
  const emit = defineEmits<{ sent: [] }>()

  const { t } = useI18n()
  const { describe } = useApiError()

  const MAX_CONTENT = 10_000
  const MIN_PASSPHRASE = 8

  const content = ref('')
  const passphrase = ref('')
  const passphraseRepeat = ref('')
  const busy = ref(false)
  const error = ref<string | null>(null)

  const requesterLabel = computed(
    () => props.requesterName || t('secret_requests.answer.the_requester'),
  )

  const blockers = computed<string[]>(() => {
    const out: string[] = []
    if (!content.value) out.push(t('secrets.create.blockers.no_content'))
    else if (content.value.length > MAX_CONTENT) out.push(t('secrets.create.blockers.too_long'))
    if (passphrase.value) {
      if (passphrase.value.length < MIN_PASSPHRASE)
        out.push(t('secrets.create.blockers.passphrase_short', { n: MIN_PASSPHRASE }))
      else if (passphrase.value !== passphraseRepeat.value)
        out.push(t('secrets.create.blockers.passphrase_mismatch'))
    }
    return out
  })
  const canSubmit = computed(() => blockers.value.length === 0 && !busy.value)

  function insertGenerated(value: string) {
    content.value = content.value ? `${content.value}\n${value}` : value
  }

  async function onSubmit() {
    if (!canSubmit.value) return
    busy.value = true
    error.value = null
    try {
      await props.submit(content.value, passphrase.value || null)
      // The text is not kept a moment longer than it has to be.
      content.value = ''
      passphrase.value = ''
      passphraseRepeat.value = ''
      emit('sent')
    } catch (err) {
      error.value = describe(err)
    } finally {
      busy.value = false
    }
  }
</script>

<style scoped>
  .answer-form {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-3);
    max-width: 640px;
  }

  .who {
    margin: 0;
  }

  .content-input {
    resize: vertical;
    min-height: 7rem;
    line-height: 1.5;
  }

  .own-passphrase summary {
    cursor: pointer;
    font-size: var(--fh-text-body-sm);
    color: var(--fh-ink-soft);
  }

  .pp-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--fh-space-3);
  }

  .blockers {
    font-size: var(--fh-text-body-sm);
    color: var(--fh-ink-soft);
  }

  .blockers ul {
    margin: 0;
    padding-left: var(--fh-space-4);
  }

  .answer-form .fh-btn {
    align-self: flex-start;
  }

  @media (max-width: 720px) {
    .pp-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
