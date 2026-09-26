<!-- Sessions › Policy tab: the session tunables (token lifetimes, per-user
     session cap) that used to sit in a group on the Advanced page, next to the
     live session list they govern. Written through the registry endpoint; see
     config/adminTunablePlacement.ts. -->
<template>
  <div class="fh-page" data-density="operator">
    <p class="fh-field-help intro">{{ t('admin_sessions_policy.intro') }}</p>
    <TunableFields route="admin-settings-sessions" />
  </div>
</template>

<script setup lang="ts">
  import { useI18n } from 'vue-i18n'

  import TunableFields from '@/components/admin/TunableFields.vue'

  const { t } = useI18n()
</script>

<style scoped>
  .intro {
    margin: 0 0 var(--fh-space-3);
    max-width: 64ch;
  }
</style>
