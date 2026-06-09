<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute } from 'vue-router'

import { reportPage404 } from '@/api/telemetry'
import AuthCanvas from '@/components/AuthCanvas.vue'

const route = useRoute()

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
    <span class="fh-eyebrow fh-rise" data-stagger="1">404 · not found</span>
    <h1 class="fh-display fh-rise" data-stagger="2">A heron flew off with this page.</h1>
    <p class="fh-rise" data-stagger="2">Try the home page or sign in.</p>
    <RouterLink to="/" class="fh-btn fh-rise" data-stagger="3" style="margin-top: 1rem">
      ← Home
    </RouterLink>
  </AuthCanvas>
</template>
