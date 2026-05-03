<script setup lang="ts">
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import { useI18n } from 'vue-i18n'

import type { SessionRecord } from '@/types/api'

dayjs.extend(relativeTime)

const props = defineProps<{ session: SessionRecord }>()
const emit = defineEmits<{ revoke: [id: number] }>()
const { t } = useI18n()

function uaShort(ua: string | null): string {
  if (!ua) return t('account.session_unknown_ua')
  // Tiny heuristic — just show the browser family + OS hint, not the full UA.
  const br = /Edg\//.test(ua)
    ? 'Edge'
    : /Chrome\//.test(ua)
      ? 'Chrome'
      : /Safari\//.test(ua) && !/Chrome\//.test(ua)
        ? 'Safari'
        : /Firefox\//.test(ua)
          ? 'Firefox'
          : /curl\//.test(ua)
            ? 'curl'
            : /python|httpx/i.test(ua)
              ? 'Python'
              : 'Browser'
  const os = /Windows/.test(ua)
    ? 'Windows'
    : /Mac OS X|Macintosh/.test(ua)
      ? 'macOS'
      : /Linux/.test(ua)
        ? 'Linux'
        : /Android/.test(ua)
          ? 'Android'
          : /iPhone|iPad/.test(ua)
            ? 'iOS'
            : ''
  return os ? `${br} · ${os}` : br
}
</script>

<template>
  <li class="sr">
    <div class="sr-left">
      <span class="sr-ua">{{ uaShort(session.created_ua) }}</span>
      <span v-if="session.is_current" class="sr-pill">{{ $t('account.session_current') }}</span>
    </div>
    <div class="sr-right">
      <span class="sr-meta">
        <span class="sr-ip" v-if="session.created_ip">{{ session.created_ip }}</span>
        <span class="sr-when" :title="session.created_at">
          {{ dayjs(session.created_at).fromNow() }}
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
