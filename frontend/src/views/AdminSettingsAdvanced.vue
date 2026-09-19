<!-- Data retention & storage. What is left of the old "Advanced" page once
     every other registry group moved to the page of its task (see
     config/adminTunablePlacement.ts): the retention windows and the low-disk
     thresholds. Same URL, honest name. -->
<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import AdminPageHeader from '@/components/admin/AdminPageHeader.vue'
import TunableFields from '@/components/admin/TunableFields.vue'
import { ADVANCED_ROUTE } from '@/config/adminTunablePlacement'

const { t } = useI18n()
</script>

<template>
  <div class="advanced-settings" data-density="operator">
    <AdminPageHeader />
    <p class="fh-field-help intro">{{ t('admin_advanced.intro') }}</p>
    <TunableFields :route="ADVANCED_ROUTE" :headings="true" />
  </div>
</template>

<style scoped>
.advanced-settings { max-width: none; }
.intro { margin-bottom: var(--fh-space-4); max-width: 64ch; }
</style>
