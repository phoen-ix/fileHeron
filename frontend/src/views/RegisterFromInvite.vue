<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import AuthCanvas from '@/components/AuthCanvas.vue'
import PasswordStrength from '@/components/PasswordStrength.vue'
import { useApiError } from '@/composables/useApiError'
import { useAuthStore } from '@/stores/auth'
import type { Locale } from '@/types/api'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const { describe } = useApiError()

const token = String(route.params.token ?? '')
const displayName = ref('')
const password = ref('')
const locale = ref<Locale>('en')
const error = ref<string | null>(null)
const submitting = ref(false)

async function onSubmit() {
  error.value = null
  submitting.value = true
  try {
    await auth.registerFromInvite({
      token,
      password: password.value,
      display_name: displayName.value,
      locale: locale.value,
    })
    await router.push('/')
  } catch (e) {
    error.value = describe(e)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <AuthCanvas>
    <span class="fh-eyebrow fh-rise" data-stagger="1">invite</span>
    <h1 class="fh-display fh-rise" data-stagger="2">{{ $t('register.title') }}</h1>
    <p class="fh-rise" data-stagger="2">{{ $t('register.subtitle') }}</p>

    <form class="form fh-rise" data-stagger="3" novalidate @submit.prevent="onSubmit">
      <div class="fh-field">
        <label class="fh-field-label" for="reg-name">{{ $t('common.display_name') }}</label>
        <input
          id="reg-name"
          v-model="displayName"
          class="fh-field-input"
          maxlength="120"
          :placeholder="$t('register.name_placeholder')"
          required
        />
      </div>

      <div class="fh-field">
        <label class="fh-field-label" for="reg-pw">{{ $t('common.new_password') }}</label>
        <input
          id="reg-pw"
          v-model="password"
          class="fh-field-input"
          type="password"
          autocomplete="new-password"
          minlength="12"
          required
        />
        <span class="fh-field-help">{{ $t('register.password_help') }}</span>
        <PasswordStrength :password="password" />
      </div>

      <div class="fh-field locale-row">
        <label class="fh-field-label">{{ $t('common.language') }}</label>
        <div class="locale-pick">
          <button
            type="button"
            class="locale-opt"
            :class="{ active: locale === 'en' }"
            :aria-pressed="locale === 'en'"
            @click="locale = 'en'"
          >
            English
          </button>
          <button
            type="button"
            class="locale-opt"
            :class="{ active: locale === 'de' }"
            :aria-pressed="locale === 'de'"
            @click="locale = 'de'"
          >
            Deutsch
          </button>
        </div>
      </div>

      <div v-if="error" class="fh-notice" data-tone="error" role="alert">{{ error }}</div>

      <div class="actions">
        <button type="submit" class="fh-btn" :disabled="submitting">
          {{ $t('register.submit') }} <span aria-hidden="true">→</span>
        </button>
      </div>
    </form>
  </AuthCanvas>
</template>

<style scoped>
.form {
  margin-top: var(--fh-space-5);
}

.locale-row {
  margin-top: var(--fh-space-4);
}

.locale-pick {
  display: inline-flex;
  gap: var(--fh-space-3);
  margin-top: var(--fh-space-1);
}

.locale-opt {
  background: none;
  border: 1px solid var(--fh-hairline-strong);
  font: inherit;
  color: var(--fh-ink);
  padding: var(--fh-space-1) var(--fh-space-3);
  cursor: pointer;
  border-radius: var(--fh-radius-sm);
  transition: all var(--fh-duration-fast) var(--fh-easing);
}

.locale-opt.active {
  background: var(--fh-ink);
  color: var(--fh-paper);
  border-color: var(--fh-ink);
}

.actions {
  margin-top: var(--fh-space-4);
}
</style>
