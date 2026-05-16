<template>
  <div class="admin-shell" data-density="operator">
    <aside class="admin-sidebar">
      <span class="sidebar-eyebrow">{{ t('admin.eyebrow') }}</span>
      <nav class="sidebar-nav" :aria-label="t('admin.nav_label')">
        <RouterLink :to="{ name: 'admin-users' }" class="nav-link">
          {{ t('admin.nav.users') }}
        </RouterLink>
        <RouterLink :to="{ name: 'admin-groups' }" class="nav-link">
          {{ t('admin.nav.groups') }}
        </RouterLink>
        <RouterLink :to="{ name: 'admin-audit' }" class="nav-link">
          {{ t('admin.nav.audit') }}
        </RouterLink>
        <RouterLink :to="{ name: 'admin-file-history' }" class="nav-link">
          {{ t('admin.nav.file_history') }}
        </RouterLink>
        <RouterLink :to="{ name: 'admin-quarantine' }" class="nav-link">
          {{ t('admin.nav.quarantine') }}
        </RouterLink>
        <RouterLink :to="{ name: 'admin-api-tokens' }" class="nav-link">
          {{ t('admin.nav.api_tokens') }}
        </RouterLink>
        <RouterLink :to="{ name: 'admin-settings' }" class="nav-link">
          {{ t('admin.nav.settings') }}
        </RouterLink>
        <RouterLink :to="{ name: 'admin-system' }" class="nav-link">
          {{ t('admin.nav.system') }}
        </RouterLink>
      </nav>
    </aside>
    <div class="admin-content">
      <RouterView />
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
</script>

<style scoped>
.admin-shell {
  display: grid;
  grid-template-columns: 200px 1fr;
  min-height: calc(100vh - 60px);
  max-width: var(--fh-max-width-page);
  margin: 0 auto;
  padding: 0 var(--fh-page-gutter);
  gap: var(--fh-space-5);
}

.admin-sidebar {
  padding: var(--fh-space-5) 0;
  border-right: 1px solid var(--fh-hairline);
}

.sidebar-eyebrow {
  display: block;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: var(--fh-subtle);
  margin-bottom: var(--fh-space-3);
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-link {
  font-family: var(--fh-font-body);
  font-size: var(--fh-text-body-md);
  color: var(--fh-ink-soft);
  text-decoration: none;
  padding: var(--fh-space-2) var(--fh-space-3);
  border-left: 2px solid transparent;
  transition:
    color var(--fh-duration-fast) var(--fh-easing),
    border-color var(--fh-duration-fast) var(--fh-easing),
    background var(--fh-duration-fast) var(--fh-easing);
}

.nav-link:hover {
  color: var(--fh-ink);
  background: var(--fh-paper-raised);
}

.nav-link.router-link-active {
  color: var(--fh-ink);
  border-left-color: var(--fh-accent);
  background: var(--fh-paper-raised);
}

.admin-content {
  padding: var(--fh-space-4) 0;
}

@media (max-width: 720px) {
  .admin-shell {
    grid-template-columns: 1fr;
  }
  .admin-sidebar {
    border-right: none;
    border-bottom: 1px solid var(--fh-hairline);
    padding: var(--fh-space-3) 0;
  }
  .sidebar-nav {
    flex-direction: row;
    flex-wrap: wrap;
    gap: var(--fh-space-2);
  }
  .nav-link {
    border-left: none;
    border-bottom: 2px solid transparent;
  }
  .nav-link.router-link-active {
    border-left: none;
    border-bottom-color: var(--fh-accent);
  }
}
</style>
