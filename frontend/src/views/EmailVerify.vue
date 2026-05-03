<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import AuthCanvas from '@/components/AuthCanvas.vue'
import { verifyEmail } from '@/api/auth'
import { useApiError } from '@/composables/useApiError'

const route = useRoute()
const { describe } = useApiError()

type State = 'verifying' | 'ok' | 'error'
const state = ref<State>('verifying')
const error = ref<string | null>(null)

onMounted(async () => {
  const token = String(route.params.token ?? '')
  try {
    await verifyEmail({ token })
    state.value = 'ok'
  } catch (e) {
    state.value = 'error'
    error.value = describe(e)
  }
})
</script>

<template>
  <AuthCanvas>
    <span class="fh-eyebrow fh-rise" data-stagger="1">/ verify</span>

    <template v-if="state === 'verifying'">
      <h1 class="fh-display fh-rise" data-stagger="2">{{ $t('verify.verifying') }}</h1>
    </template>

    <template v-else-if="state === 'ok'">
      <h1 class="fh-display fh-rise" data-stagger="2">{{ $t('verify.ok_title') }}</h1>
      <p class="fh-rise" data-stagger="2">{{ $t('verify.ok_subtitle') }}</p>
      <RouterLink to="/login" class="fh-btn fh-rise" data-stagger="3" style="margin-top: 1rem">
        {{ $t('verify.to_login') }} <span aria-hidden="true">→</span>
      </RouterLink>
    </template>

    <template v-else>
      <h1 class="fh-display fh-rise" data-stagger="2">—</h1>
      <div class="fh-notice fh-rise" data-stagger="2" data-tone="error" role="alert">
        {{ error }}
      </div>
      <RouterLink to="/login" class="fh-btn-text fh-rise" data-stagger="3">
        ← {{ $t('common.back') }}
      </RouterLink>
    </template>
  </AuthCanvas>
</template>
