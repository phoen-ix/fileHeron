<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import type { SessionRecord } from '@/types/api'
import { formatInSiteTime } from '@/utils/datetime'
import { uaShort } from '@/utils/ua'

defineProps<{ session: SessionRecord }>()
const emit = defineEmits<{ revoke: [id: number] }>()
const { t, locale } = useI18n()
</script>

<template>
  <li class="sr">
    <div class="sr-left">
      <span class="sr-ua">{{ uaShort(session.created_ua, t('account.session_unknown_ua')) }}</span>
      <span v-if="session.is_current" class="sr-pill">{{ $t('account.session_current') }}</span>
    </div>
    <div class="sr-right">
      <span class="sr-meta">
        <span v-if="session.created_ip" class="sr-ip">{{ session.created_ip }}</span>
        <span class="sr-when">
          {{ t('account.session_last_active') }}:
          {{ formatInSiteTime(session.last_used_at ?? session.created_at, locale) }}
        </span>
      </span>
      <button
        v-if="!session.is_current"
        type="button"
        class="fh-btn-text"
        @click="emit('revoke', session.id)"
      >
        {{ $t('account.session_revoke') }}
      </button>
    </div>
  </li>
</template>

<style scoped>
.sr {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--fh-space-3);
  padding: var(--fh-space-3) 0;
  border-top: 1px solid var(--fh-hairline);
  list-style: none;
}

.sr:last-child {
  border-bottom: 1px solid var(--fh-hairline);
}

.sr-left {
  display: inline-flex;
  align-items: baseline;
  gap: var(--fh-space-2);
}

.sr-ua {
  font-size: var(--fh-text-body-md);
  color: var(--fh-ink);
}

.sr-pill {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--fh-accent);
  border: 1px solid var(--fh-accent);
  padding: 1px 6px;
}

.sr-right {
  display: inline-flex;
  align-items: baseline;
  gap: var(--fh-space-3);
}

.sr-meta {
  display: inline-flex;
  align-items: baseline;
  gap: var(--fh-space-3);
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
}

.sr-ip {
  letter-spacing: 0.04em;
}
</style>
