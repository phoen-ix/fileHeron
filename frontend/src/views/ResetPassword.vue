<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'

import AuthCanvas from '@/components/AuthCanvas.vue'
import PasswordStrength from '@/components/PasswordStrength.vue'
import { resetPassword } from '@/api/auth'
import { useApiError } from '@/composables/useApiError'

const route = useRoute()
const { describe } = useApiError()

const token = String(route.params.token ?? '')
const newPassword = ref('')
const submitting = ref(false)
const done = ref(false)
const error = ref<string | null>(null)

async function onSubmit() {
  error.value = null
  submitting.value = true
  try {
    await resetPassword({ token, new_password: newPassword.value })
    done.value = true
  } catch (e) {
    error.value = describe(e)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <AuthCanvas>
    <template v-if="!done">
      <span class="fh-eyebrow fh-rise" data-stagger="1">{{ $t('reset.title') }}</span>
      <h1 class="fh-display fh-rise" data-stagger="2">{{ $t('reset.title') }}</h1>
      <p class="fh-rise" data-stagger="2">{{ $t('reset.subtitle') }}</p>

      <form class="form fh-rise" data-stagger="3" @submit.prevent="onSubmit">
        <div class="fh-field">
          <label class="fh-field-label" for="rp-pw">{{ $t('common.new_password') }}</label>
          <input
            id="rp-pw"
            v-model="newPassword"
            class="fh-field-input"
            type="password"
            autocomplete="new-password"
            minlength="12"
            required
          />
          <PasswordStrength :password="newPassword" />
        </div>

        <div v-if="error" class="fh-notice" data-tone="error" role="alert">{{ error }}</div>

        <div class="actions">
          <button type="submit" class="fh-btn" :disabled="submitting">
            {{ $t('reset.submit') }} <span aria-hidden="true">→</span>
          </button>
        </div>
      </form>
    </template>

    <template v-else>
      <span class="fh-eyebrow fh-rise" data-stagger="1">done</span>
      <h1 class="fh-display fh-rise" data-stagger="2">{{ $t('reset.done_title') }}</h1>
      <p class="fh-rise" data-stagger="2">{{ $t('reset.done_subtitle') }}</p>
      <RouterLink to="/login" class="fh-btn fh-rise" data-stagger="3" style="margin-top: 1rem">
        {{ $t('reset.to_login') }} <span aria-hidden="true">→</span>
      </RouterLink>
    </template>
  </AuthCanvas>
</template>

<style scoped>
.form {
  margin-top: var(--fh-space-5);
}
.actions {
  margin-top: var(--fh-space-4);
}
</style>
