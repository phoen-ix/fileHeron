<!-- Sign-in policies › Passwords & brute force: the account-lockout and
     per-address sign-in limits plus the HIBP password check. These were the
     "Rate limits & lockout" and "Security" groups on the Advanced page - a
     jargon name nobody's task starts from; the task is "stop password
     guessing". The public-link brute-force triple from the same registry
     group lives on the Public links page instead. -->
<template>
  <div class="fh-page" data-density="operator">
    <p class="fh-field-help intro">{{ t('admin_sign_in.intro') }}</p>
    <div class="fh-notice" data-tone="accent">{{ t('admin_sign_in.scope_note') }}</div>
    <TunableFields route="admin-settings-sign-in" :headings="true" />
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
