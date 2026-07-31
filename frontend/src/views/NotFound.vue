<script setup lang="ts">
import { onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { reportPage404 } from '@/api/telemetry'
import AuthCanvas from '@/components/AuthCanvas.vue'

const route = useRoute()
// The 404 page was the only view in the app with hardcoded English copy, in a
// DE/EN product - and it is the page a German user is most likely to reach by
// accident (audit 2026-07-30, fe-correct-13).
const { t } = useI18n()

// Report the unmatched path so it lands in the admin error log (the backend never
// sees it - nginx serves the SPA shell for unknown page paths). Best-effort: a
// failed beacon must never break the 404 page.
onMounted(() => {
  // Best-effort, fully swallowed (including async rejection).
  reportPage404(route.fullPath).catch(() => {})
})
</script>

<template>
  <AuthCanvas>
    <span class="fh-eyebrow fh-rise" data-stagger="1">{{ t('not_found.eyebrow') }}</span>
    <h1 class="fh-display fh-rise" data-stagger="2">{{ t('not_found.headline') }}</h1>
    <p class="fh-rise" data-stagger="2">{{ t('not_found.body') }}</p>
    <RouterLink to="/" class="fh-btn fh-rise" data-stagger="3" style="margin-top: 1rem">
      ← {{ t('not_found.home_cta') }}
    </RouterLink>
  </AuthCanvas>
</template>
