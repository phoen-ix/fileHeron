<script setup lang="ts">
  import { onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'
  import { useRoute, useRouter } from 'vue-router'

  import AuthCanvas from '@/components/AuthCanvas.vue'
  import { completeSetup, getSetupStatus } from '@/api/setup'
  import { useApiError } from '@/composables/useApiError'
  import { useAuthStore } from '@/stores/auth'

  const { t } = useI18n()
  const route = useRoute()
  const router = useRouter()
  const { describe } = useApiError()
  const auth = useAuthStore()

  const email = ref('')
  const displayName = ref('')
  const password = ref('')
  const passwordConfirm = ref('')
  const submitting = ref(false)
  const errorMsg = ref<string | null>(null)

  // install.sh prints /setup?token=...; the backend refuses the wizard without it
  // when SETUP_TOKEN is configured. Taken from the URL once and then dropped from
  // the address bar, so it does not linger in history. The field only appears
  // when the server wants a token the URL did not bring.
  const setupToken = ref(typeof route.query.token === 'string' ? route.query.token : '')
  const tokenFromUrl = setupToken.value !== ''
  const tokenRequired = ref(false)

  onMounted(async () => {
    if (tokenFromUrl) void router.replace({ query: {} })
    try {
      const { data } = await getSetupStatus()
      tokenRequired.value = data.token_required
    } catch {
      /* the submit reports any refusal */
    }
  })

  async function onSubmit() {
    errorMsg.value = null
    if (password.value !== passwordConfirm.value) {
      errorMsg.value = t('setup.error.password_mismatch')
      return
    }
    if (password.value.length < 12) {
      errorMsg.value = t('setup.error.password_too_short')
      return
    }
    submitting.value = true
    try {
      await completeSetup({
        email: email.value.trim(),
        password: password.value,
        display_name: displayName.value.trim(),
        setup_token: setupToken.value.trim() || null,
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
          minlength="12"
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

      <label v-if="tokenRequired && !tokenFromUrl" class="fh-field">
        <span class="fh-field-label">{{ t('setup.token_label') }}</span>
        <input
          v-model="setupToken"
          type="text"
          class="fh-field-input fh-mono"
          autocomplete="off"
          spellcheck="false"
          required
        />
        <span class="fh-field-help">{{ t('setup.token_help') }}</span>
      </label>

      <div v-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

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
