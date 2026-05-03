<script setup lang="ts">
import { ref } from 'vue'

import AuthCanvas from '@/components/AuthCanvas.vue'
import { forgotPassword } from '@/api/auth'

const email = ref('')
const sent = ref(false)
const submitting = ref(false)

async function onSubmit() {
  submitting.value = true
  try {
    await forgotPassword({ email: email.value })
    sent.value = true
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <AuthCanvas>
    <template v-if="!sent">
      <span class="fh-eyebrow fh-rise" data-stagger="1">{{ $t('forgot.title') }}</span>
      <h1 class="fh-display fh-rise" data-stagger="2">{{ $t('forgot.title') }}</h1>
      <p class="fh-rise" data-stagger="2">{{ $t('forgot.subtitle') }}</p>

      <form class="form fh-rise" data-stagger="3" @submit.prevent="onSubmit">
        <div class="fh-field">
          <label class="fh-field-label" for="fp-email">{{ $t('common.email') }}</label>
          <input
            id="fp-email"
            v-model="email"
            class="fh-field-input"
            type="email"
            autocomplete="username"
            required
          />
        </div>
        <div class="actions">
          <button type="submit" class="fh-btn" :disabled="submitting">
            {{ $t('forgot.submit') }} <span aria-hidden="true">→</span>
          </button>
          <RouterLink to="/login" class="back">
            {{ $t('common.back') }}
          </RouterLink>
        </div>
      </form>
    </template>

    <template v-else>
      <span class="fh-eyebrow fh-rise" data-stagger="1">/ inbox</span>
      <h1 class="fh-display fh-rise" data-stagger="2">{{ $t('forgot.sent_title') }}</h1>
      <p class="fh-rise" data-stagger="2">{{ $t('forgot.sent_subtitle') }}</p>
      <RouterLink to="/login" class="fh-btn-text fh-rise" data-stagger="3">
        ← {{ $t('common.back') }}
      </RouterLink>
    </template>
  </AuthCanvas>
</template>

<style scoped>
.form {
  margin-top: var(--fh-space-5);
}
.actions {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--fh-space-3);
  margin-top: var(--fh-space-4);
}
.back {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--fh-subtle);
  text-decoration: none;
}
.back:hover {
  color: var(--fh-accent);
}
</style>
