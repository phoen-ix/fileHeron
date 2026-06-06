<template>
  <a
    v-if="linkUrl"
    :href="linkUrl"
    class="brand-logo-link"
    target="_blank"
    rel="noopener noreferrer"
  >
    <img :src="src" :alt="alt" class="brand-logo" :data-size="size" />
  </a>
  <img v-else :src="src" :alt="alt" class="brand-logo" :data-size="size" />
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    src: string
    alt?: string
    linkUrl?: string | null
    size?: 'sm' | 'md'
  }>(),
  { alt: '', linkUrl: null, size: 'sm' },
)
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
