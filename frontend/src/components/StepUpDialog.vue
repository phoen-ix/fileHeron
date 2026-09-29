<script setup lang="ts">
  /* The one place the SPA asks for the signed-in user's own password before a
   * protected action (the backend's step-up re-auth, services/step_up.py).
   * Modelled on the Update dialog: the parent runs the request with the
   * password this emits, keeps the dialog open while `busy`, and passes a
   * wrong password or any other failure back as `error`, so it shows inside the
   * dialog instead of leaving it looking as if nothing happened. */
  import { computed, nextTick, ref, watch } from 'vue'
  import { useI18n } from 'vue-i18n'

  import { useEscapeToClose } from '@/composables/useEscapeToClose'

  const props = withDefaults(
    defineProps<{
      open: boolean
      title?: string
      message?: string
      confirmLabel?: string
      busy?: boolean
      error?: string | null
      danger?: boolean
    }>(),
    { title: '', message: '', confirmLabel: '', busy: false, error: null, danger: false },
  )

  const emit = defineEmits<{ confirm: [password: string]; cancel: [] }>()

  const { t } = useI18n()
  const password = ref('')
  const input = ref<HTMLInputElement | null>(null)

  // Never carry a password from one opening to the next.
  watch(
    () => props.open,
    async (open) => {
      password.value = ''
      if (open) {
        await nextTick()
        input.value?.focus()
      }
    },
    { immediate: true },
  )

  function cancel() {
    if (!props.busy) emit('cancel')
  }

  function submit() {
    if (!props.busy && password.value) emit('confirm', password.value)
  }

  // Escape has to be bound on the document; see useEscapeToClose.
  useEscapeToClose(
    computed(() => props.open),
    () => cancel(),
  )
</script>

<template>
  <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- modal backdrop: click-outside is a convenience, Escape is the keyboard path; revisited with the modal focus work -->
  <div
    v-if="open"
    class="fh-modal-backdrop"
    role="dialog"
    aria-modal="true"
    :aria-label="title || t('common.confirm_your_password')"
    data-testid="step-up-dialog"
    @click.self="cancel"
    @keydown.esc="cancel"
  >
    <div class="fh-modal fh-modal--small">
      <h2 class="modal-h2">{{ title || t('common.confirm_your_password') }}</h2>
      <p v-if="message" class="message">{{ message }}</p>
      <slot />
      <form @submit.prevent="submit">
        <label class="fh-field">
          <span class="fh-field-label">{{ t('common.current_password') }}</span>
          <input
            ref="input"
            v-model="password"
            type="password"
            class="fh-field-input"
            autocomplete="current-password"
            required
            data-testid="step-up-password"
          />
        </label>
        <div v-if="error" class="fh-notice" data-tone="error" role="alert">{{ error }}</div>
        <div class="actions">
          <button type="button" class="fh-btn-ghost fh-btn" :disabled="busy" @click="cancel">
            {{ t('common.cancel') }}
          </button>
          <button
            type="submit"
            class="fh-btn"
            :class="{ 'fh-btn-danger': danger }"
            :disabled="busy || !password"
            data-testid="step-up-confirm"
          >
            {{ busy ? t('common.loading') : confirmLabel || t('common.confirm') }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
  .fh-modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(26, 29, 36, 0.4);
    display: grid;
    place-items: center;
    z-index: 300;
  }

  .fh-modal {
    background: var(--fh-paper);
    border: 1px solid var(--fh-hairline-strong);
    box-shadow: 0 8px 40px rgba(26, 29, 36, 0.15);
    padding: var(--fh-space-5);
    width: min(420px, 92vw);
  }

  .modal-h2 {
    font-family: var(--fh-font-display);
    font-size: 1.25rem;
    margin: 0 0 var(--fh-space-2);
  }

  .message {
    margin: 0 0 var(--fh-space-4);
    color: var(--fh-ink);
    line-height: 1.5;
  }

  form {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-3);
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--fh-space-3);
  }
</style>
