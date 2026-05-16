<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import AuthCanvas from '@/components/AuthCanvas.vue'
import { completeSetup } from '@/api/setup'
import { useApiError } from '@/composables/useApiError'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
const router = useRouter()
const { describe } = useApiError()
const auth = useAuthStore()

const email = ref('')
const displayName = ref('')
const password = ref('')
const passwordConfirm = ref('')
const submitting = ref(false)
const errorMsg = ref<string | null>(null)

async function onSubmit() {
  errorMsg.value = null
  if (password.value !== passwordConfirm.value) {
    errorMsg.value = t('setup.error.password_mismatch')
    return
  }
  if (password.value.length < 8) {
    errorMsg.value = t('setup.error.password_too_short')
    return
  }
  submitting.value = true
  try {
    await completeSetup({
      email: email.value.trim(),
      password: password.value,
      display_name: displayName.value.trim(),
    })
    // Auto-login the new admin so they go straight into the app.
    await auth.login(email.value.trim(), password.value)
    // Flip the cached flag so the router stops redirecting here.
    auth.setupRequired = false
    await router.push({ name: 'home' })
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <AuthCanvas>
    <h1 class="fh-display-lg setup-title">{{ t('setup.title') }}</h1>
    <p class="fh-field-help setup-intro">{{ t('setup.intro') }}</p>

    <form class="setup-form" @submit.prevent="onSubmit">
      <label class="fh-field">
        <span class="fh-field-label">{{ t('common.email') }}</span>
        <input
          v-model.trim="email"
          type="email"
          class="fh-field-input"
          autocomplete="username"
          required
          autofocus
        />
      </label>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('common.display_name') }}</span>
        <input
          v-model.trim="displayName"
          type="text"
          class="fh-field-input"
          maxlength="120"
          required
        />
        <span class="fh-field-help">{{ t('setup.display_name_help') }}</span>
      </label>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('common.password') }}</span>
        <input
          v-model="password"
          type="password"
          class="fh-field-input"
          autocomplete="new-password"
          required
          minlength="8"
        />
        <span class="fh-field-help">{{ t('setup.password_help') }}</span>
      </label>

      <label class="fh-field">
        <span class="fh-field-label">{{ t('setup.password_confirm') }}</span>
        <input
          v-model="passwordConfirm"
          type="password"
          class="fh-field-input"
          autocomplete="new-password"
          required
        />
      </label>

      <div v-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

      <button type="submit" class="fh-btn" :disabled="submitting || !email || !password">
        {{ submitting ? t('common.loading') : t('setup.submit') }}
      </button>
    </form>
  </AuthCanvas>
</template>

<style scoped>
.setup-title {
  margin: 0 0 var(--fh-space-2);
}
.setup-intro {
  margin: 0 0 var(--fh-space-4);
  max-width: 48ch;
}
.setup-form {
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-3);
  max-width: 24rem;
}
</style>
