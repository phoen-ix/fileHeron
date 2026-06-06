<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import AuthCanvas from '@/components/AuthCanvas.vue'
import { cancelEmailChange } from '@/api/auth'
import { useApiError } from '@/composables/useApiError'

const route = useRoute()
const { describe } = useApiError()

type State = 'cancelling' | 'ok' | 'error'
const state = ref<State>('cancelling')
const error = ref<string | null>(null)

onMounted(async () => {
  const token = String(route.params.token ?? '')
  try {
    await cancelEmailChange({ token })
    state.value = 'ok'
  } catch (e) {
    state.value = 'error'
    error.value = describe(e)
  }
})
</script>

<template>
  <AuthCanvas>
    <span class="fh-eyebrow fh-rise" data-stagger="1">/ {{ $t('cancel_email_change.eyebrow') }}</span>

    <template v-if="state === 'cancelling'">
      <h1 class="fh-display fh-rise" data-stagger="2">{{ $t('cancel_email_change.cancelling') }}</h1>
    </template>

    <template v-else-if="state === 'ok'">
      <h1 class="fh-display fh-rise" data-stagger="2">{{ $t('cancel_email_change.ok_title') }}</h1>
      <p class="fh-rise" data-stagger="2">{{ $t('cancel_email_change.ok_subtitle') }}</p>
      <RouterLink to="/login" class="fh-btn fh-rise" data-stagger="3" style="margin-top: 1rem">
        {{ $t('cancel_email_change.to_login') }} <span aria-hidden="true">→</span>
      </RouterLink>
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
