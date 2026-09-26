<!-- Anomaly detection: the four thresholds behind the hourly `anomaly_check`
     cron. They only ever existed as a group inside the Advanced page, so a
     security feature with its own webhook event had no page. It is advisory
     by design - a finding alerts an admin and never blocks anything; there is
     no wiring from a finding to the scan guard (CLAUDE.md §Anomaly detection). -->
<template>
  <div class="fh-page anomaly-page" data-density="operator">
    <AdminPageHeader>
      <p class="fh-field-help intro">{{ t('admin_anomaly.intro') }}</p>
    </AdminPageHeader>

    <div class="fh-notice" data-tone="accent">{{ t('admin_anomaly.advisory') }}</div>

    <TunableFields route="admin-settings-anomaly" :headings="false" />

    <p class="fh-field-help cadence">
      {{ t('admin_anomaly.cadence_note') }}
      <RouterLink :to="{ name: 'admin-scheduled-tasks' }">{{
        t('admin.nav.scheduled_tasks')
      }}</RouterLink>
    </p>
  </div>
</template>

<script setup lang="ts">
  import { useI18n } from 'vue-i18n'

  import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
  import TunableFields from '@/components/admin/TunableFields.vue'

  const { t } = useI18n()
</script>

<style scoped>
  .anomaly-page {
    max-width: 44rem;
  }

  .intro {
    margin: var(--fh-space-1) 0 0;
    max-width: 64ch;
  }

  .cadence {
    margin-top: var(--fh-space-4);
  }
</style>
