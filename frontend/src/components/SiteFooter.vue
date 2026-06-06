<template>
  <footer v-if="showImprint || showPrivacy" class="site-footer">
    <nav class="site-footer-links" :aria-label="t('footer.aria')">
      <RouterLink v-if="showImprint" :to="{ name: 'imprint' }" class="site-footer-link">
        {{ t('footer.imprint') }}
      </RouterLink>
      <RouterLink v-if="showPrivacy" :to="{ name: 'privacy' }" class="site-footer-link">
        {{ t('footer.privacy') }}
      </RouterLink>
    </nav>
  </footer>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { useSiteStore } from '@/stores/site'

const { t } = useI18n()
const site = useSiteStore()

const showImprint = computed(() => site.legal.imprint_enabled)
const showPrivacy = computed(() => site.legal.privacy_enabled)
</script>

<style scoped>
.site-footer {
  border-top: 1px solid var(--fh-hairline);
  padding: var(--fh-space-3) var(--fh-page-gutter);
  display: flex;
  justify-content: center;
}
.site-footer-links {
  display: flex;
  gap: var(--fh-space-4);
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.site-footer-link {
  color: var(--fh-subtle);
  text-decoration: none;
}
.site-footer-link:hover {
  color: var(--fh-ink);
  text-decoration: underline;
}
</style>
