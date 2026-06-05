<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

interface SettingsSection {
  name: string
  to: { name: string }
  description: string
}

const sections: SettingsSection[] = [
  {
    name: 'general',
    to: { name: 'admin-settings-general' },
    description: 'admin_settings.sections.general',
  },
  {
    name: 'sso',
    to: { name: 'admin-settings-sso' },
    description: 'admin_settings.sections.sso',
  },
  {
    name: 'twofa',
    to: { name: 'admin-settings-twofa' },
    description: 'admin_settings.sections.twofa',
  },
  {
    name: 'api_tokens',
    to: { name: 'admin-settings-api-tokens' },
    description: 'admin_settings.sections.api_tokens',
  },
  {
    name: 'public_links',
    to: { name: 'admin-settings-public-links' },
    description: 'admin_settings.sections.public_links',
  },
  {
    name: 'email',
    to: { name: 'admin-settings-email' },
    description: 'admin_settings.sections.email',
  },
  {
    name: 'quarantine',
    to: { name: 'admin-settings-quarantine' },
    description: 'admin_settings.sections.quarantine',
  },
  {
    name: 'email_change',
    to: { name: 'admin-settings-email-change' },
    description: 'admin_settings.sections.email_change',
  },
  {
    name: 'advanced',
    to: { name: 'admin-settings-advanced' },
    description: 'admin_settings.sections.advanced',
  },
]
</script>

<template>
  <div class="settings-index">
    <span class="fh-eyebrow">{{ t('admin_settings.eyebrow') }}</span>
    <p class="fh-field-help intro">{{ t('admin_settings.intro') }}</p>

    <ul class="section-list">
      <RouterLink
        v-for="s in sections"
        :key="s.name"
        :to="s.to"
        class="section-row"
      >
        <span class="section-name">{{ t(`admin_settings.section_titles.${s.name}`) }}</span>
        <span class="section-desc">{{ t(s.description) }}</span>
        <span class="arrow" aria-hidden="true">→</span>
      </RouterLink>
    </ul>
  </div>
</template>

<style scoped>
.settings-index {
  max-width: 720px;
}

.intro {
  margin-bottom: var(--fh-space-4);
  max-width: 60ch;
}

.section-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: var(--fh-border);
}

.section-row {
  display: grid;
  grid-template-columns: 200px 1fr auto;
  gap: var(--fh-space-3);
  align-items: center;
  padding: var(--fh-space-4) 0;
  border-bottom: var(--fh-border);
  text-decoration: none;
  color: var(--fh-ink);
  transition: background var(--fh-duration-fast) var(--fh-easing);
}

.section-row:hover {
  background: var(--fh-paper-raised);
}

.section-name {
  font-family: var(--fh-font-display);
  font-size: 1.25rem;
  color: var(--fh-ink);
}

.section-desc {
  color: var(--fh-subtle);
  font-size: var(--fh-text-body-sm);
}

.arrow {
  color: var(--fh-subtle);
  font-size: 18px;
}
</style>
