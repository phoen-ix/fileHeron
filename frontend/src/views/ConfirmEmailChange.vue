<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import AuthCanvas from '@/components/AuthCanvas.vue'
import { confirmEmailChange } from '@/api/auth'
import { useApiError } from '@/composables/useApiError'

const route = useRoute()
const { describe } = useApiError()

type State = 'confirming' | 'applied' | 'pending' | 'error'
const state = ref<State>('confirming')
const error = ref<string | null>(null)
const setPasswordRequired = ref(false)

onMounted(async () => {
  const token = String(route.params.token ?? '')
  try {
    const { data } = await confirmEmailChange({ token })
    if (data.applied) {
      state.value = 'applied'
      setPasswordRequired.value = data.set_password_required
    } else {
      state.value = 'pending'
    }
  } catch (e) {
    state.value = 'error'
    error.value = describe(e)
  }
})
</script>

<template>
  <AuthCanvas>
    <span class="fh-eyebrow fh-rise" data-stagger="1">/ {{ $t('confirm_email_change.eyebrow') }}</span>

    <template v-if="state === 'confirming'">
      <h1 class="fh-display fh-rise" data-stagger="2">{{ $t('confirm_email_change.confirming') }}</h1>
    </template>

    <template v-else-if="state === 'applied'">
      <h1 class="fh-display fh-rise" data-stagger="2">{{ $t('confirm_email_change.ok_title') }}</h1>
      <p class="fh-rise" data-stagger="2">{{ $t('confirm_email_change.ok_subtitle') }}</p>
      <p v-if="setPasswordRequired" class="fh-notice fh-rise" data-stagger="2" data-tone="info">
        {{ $t('confirm_email_change.set_password_note') }}
      </p>
      <RouterLink to="/login" class="fh-btn fh-rise" data-stagger="3" style="margin-top: 1rem">
        {{ $t('confirm_email_change.to_login') }} <span aria-hidden="true">→</span>
      </RouterLink>
    </template>

    <template v-else-if="state === 'pending'">
      <h1 class="fh-display fh-rise" data-stagger="2">{{ $t('confirm_email_change.pending_title') }}</h1>
      <p class="fh-rise" data-stagger="2">{{ $t('confirm_email_change.pending_subtitle') }}</p>
    </template>

    <template v-else>
      <h1 class="fh-display fh-rise" data-stagger="2">-</h1>
      <div class="fh-notice fh-rise" data-stagger="2" data-tone="error" role="alert">
        {{ error }}
      </div>
      <RouterLink to="/login" class="fh-btn-text fh-rise" data-stagger="3">
        ← {{ $t('common.back') }}
      </RouterLink>
    </template>
  </AuthCanvas>
</template>
