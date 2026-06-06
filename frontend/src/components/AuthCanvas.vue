<script setup lang="ts">
/* Shared layout for public auth pages. Single column, form-centered,
 * deliberately minimal - no decorative aside, no caption.
 *
 * Pages slot their form into the default slot. */

import { computed } from 'vue'

import LanguageSwitcher from '@/components/LanguageSwitcher.vue'
import BrandLogo from '@/components/BrandLogo.vue'
import BrandMark from '@/components/BrandMark.vue'
import { useSiteStore } from '@/stores/site'

const site = useSiteStore()
const showLogo = computed(
  () => site.branding.show_login && !!site.branding.logo_url,
)
</script>

<template>
  <div class="auth">
    <header class="auth-top">
      <div class="auth-brand">
        <BrandLogo
          v-if="showLogo"
          :src="site.branding.logo_url as string"
          :alt="site.appName"
          :link-url="site.branding.link_url"
          size="sm"
        />
        <BrandMark size="sm" />
      </div>
      <LanguageSwitcher />
    </header>

    <main class="auth-inner">
      <section class="auth-form">
        <slot />
      </section>
    </main>

    <footer class="auth-bottom">
      <span class="auth-foot-meta">self-hosted · GDPR-aware · MIT (planned)</span>
    </footer>
  </div>
</template>

<style scoped>
.auth {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  padding: var(--fh-space-3) var(--fh-page-gutter);
}

.auth-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fh-space-3);
  padding-bottom: var(--fh-space-3);
}

.auth-brand {
  display: flex;
  align-items: center;
  gap: var(--fh-space-2);
}

.auth-inner {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--fh-space-6) 0;
}

.auth-form {
  width: 100%;
  max-width: 420px;
}

.auth-bottom {
  border-top: 1px solid var(--fh-hairline);
  padding: var(--fh-space-3) 0;
  display: flex;
  justify-content: flex-end;
  align-items: baseline;
  gap: var(--fh-space-3);
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.auth-foot-meta {
  color: var(--fh-subtle);
}
</style>
