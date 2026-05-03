<script setup lang="ts">
import AuthCanvas from '@/components/AuthCanvas.vue'
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
