<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import { setLocale, type SupportedLocale } from '@/i18n'

const { locale, t } = useI18n()

function pick(l: SupportedLocale) {
  setLocale(l)
}
</script>

<template>
  <div class="lang" role="group" :aria-label="t('a11y.language_switcher')">
    <button
      type="button"
      class="lang-btn"
      :class="{ active: locale === 'en' }"
      :aria-pressed="locale === 'en'"
      aria-label="English"
      @click="pick('en')"
    >
      EN
    </button>
    <span aria-hidden="true">·</span>
    <button
      type="button"
      class="lang-btn"
      :class="{ active: locale === 'de' }"
      :aria-pressed="locale === 'de'"
      aria-label="Deutsch"
      @click="pick('de')"
    >
      DE
    </button>
  </div>
</template>

<style scoped>
.lang {
  display: inline-flex;
  gap: var(--fh-space-1);
  align-items: center;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  letter-spacing: 0.08em;
  color: var(--fh-subtle);
}

.lang-btn {
  background: none;
  border: none;
  font: inherit;
  color: inherit;
  padding: 2px 4px;
  cursor: pointer;
  border-bottom: 1px solid transparent;
  transition: color var(--fh-duration-fast) var(--fh-easing);
}

.lang-btn:hover {
  color: var(--fh-ink);
}

.lang-btn.active {
  color: var(--fh-ink);
  border-bottom-color: var(--fh-accent);
}
</style>
