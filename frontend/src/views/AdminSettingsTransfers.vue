<!-- Files & transfers: everything that shapes how bytes move and what a
     recipient sees. Share defaults and File preview came from the General
     grab-bag; the upload cap and the download tunables from the Advanced
     page. Each section saves on its own, like General. -->
<template>
  <div class="fh-page transfers-page" data-density="operator">
    <AdminPageHeader>
      <p class="fh-field-help intro">{{ t('admin_transfers.intro') }}</p>
    </AdminPageHeader>

    <hr class="fh-rule" />

    <section id="share-defaults" class="settings-section">
      <h2 class="settings-h2">{{ t('admin_share_defaults.title') }}</h2>
      <ShareDefaultsSection />
    </section>

    <section id="uploads" class="settings-section">
      <h2 class="settings-h2">{{ t('admin_advanced.groups.uploads') }}</h2>
      <TunableFields route="admin-settings-transfers" :groups="['uploads']" :headings="false" />
    </section>

    <section id="downloads" class="settings-section">
      <h2 class="settings-h2">{{ t('admin_advanced.groups.downloads') }}</h2>
      <TunableFields route="admin-settings-transfers" :groups="['downloads']" :headings="false" />
    </section>

    <section id="file-preview" class="settings-section">
      <h2 class="settings-h2">{{ t('admin_file_preview.title') }}</h2>
      <FilePreviewSection />
    </section>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import FilePreviewSection from '@/components/admin/FilePreviewSection.vue'
import ShareDefaultsSection from '@/components/admin/ShareDefaultsSection.vue'
import TunableFields from '@/components/admin/TunableFields.vue'

const { t } = useI18n()
</script>

<style scoped>
.transfers-page {
  max-width: 44rem;
}

.intro {
  margin: var(--fh-space-1) 0 0;
  max-width: 64ch;
}

.settings-section {
  scroll-margin-top: calc(var(--fh-app-header-height) + var(--fh-space-3));
  margin-top: var(--fh-space-5);
}

.settings-section:first-of-type {
  margin-top: var(--fh-space-4);
}

.settings-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
  letter-spacing: -0.01em;
  margin: 0 0 var(--fh-space-3);
  color: var(--fh-ink);
}
</style>
