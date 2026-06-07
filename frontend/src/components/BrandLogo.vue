<template>
  <a
    v-if="safeLink"
    :href="safeLink"
    class="brand-logo-link"
    target="_blank"
    rel="noopener noreferrer"
  >
    <img :src="src" :alt="alt" class="brand-logo" :data-size="size" />
  </a>
  <img v-else :src="src" :alt="alt" class="brand-logo" :data-size="size" />
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    src: string
    alt?: string
    linkUrl?: string | null
    size?: 'sm' | 'md'
  }>(),
  { alt: '', linkUrl: null, size: 'sm' },
)

// Only allow http(s) absolute URLs or a root-relative path as the logo link;
// reject javascript:/data:/vbscript:/etc. so an admin-set (or config-backup-
// imported) branding link can't execute script on the pre-auth login page
// where the logo renders (audit L29).
const safeLink = computed<string | null>(() => {
  const u = (props.linkUrl ?? '').trim()
  if (!u) return null
  return /^https?:\/\//i.test(u) || u.startsWith('/') ? u : null
})
</script>

<style scoped>
.brand-logo-link {
  display: inline-flex;
  align-items: center;
}
.brand-logo {
  width: auto;
  display: block;
}
.brand-logo[data-size='sm'] {
  height: 28px;
}
.brand-logo[data-size='md'] {
  height: 40px;
}
</style>
