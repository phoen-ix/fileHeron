<template>
  <li class="notif-row" :class="{ unread: !item.read_at }" tabindex="0" @keydown.enter="emit('click')" @click="emit('click')">
    <span class="notif-eyebrow fh-mono">{{ t(`notif_bell.cat.${item.category}`) }}</span>
    <span class="notif-headline">{{ headline }}</span>
    <span class="notif-time fh-mono">{{ relTime }}</span>
  </li>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { NotificationItem as NItem } from '@/types/api'

const props = defineProps<{ item: NItem }>()
const emit = defineEmits<{ click: [] }>()
const { t, locale } = useI18n()

const headline = computed(() => {
  // Each category has its own template under notif_bell.headline.{category}
  // with the payload spread. Use $t with arguments — fall back to the
  // generic line if the payload is missing keys.
  const key = `notif_bell.headline.${props.item.category}`
  const payload = props.item.payload || {}
  try {
    return t(key, payload as Record<string, unknown>)
  } catch {
    return t('notif_bell.headline.generic')
  }
})

const relTime = computed(() => {
  const created = new Date(props.item.created_at).getTime()
  const diff = Date.now() - created
  const min = Math.round(diff / 60_000)
  if (min < 1) return t('notif_bell.just_now')
  if (min < 60) return t('notif_bell.min_ago', { n: min })
  const hr = Math.round(min / 60)
  if (hr < 24) return t('notif_bell.hr_ago', { n: hr })
  return new Date(created).toLocaleDateString(
    locale.value === 'de' ? 'de-AT' : 'en-US',
    { month: 'short', day: 'numeric' },
  )
})
</script>

<style scoped>
.notif-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 12px;
  align-items: baseline;
  padding: var(--fh-space-3);
  border-bottom: var(--fh-border);
  cursor: pointer;
  transition: background var(--fh-duration-fast) var(--fh-easing);
}

.notif-row:hover {
  background: var(--fh-paper-sunk);
}

.notif-row.unread {
  background: var(--fh-accent-soft);
}

.notif-row.unread:hover {
  background: var(--fh-accent-soft);
}

.notif-row.unread::before {
  content: '';
  position: absolute;
  margin-left: -10px;
  margin-top: 6px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--fh-accent);
}

.notif-eyebrow {
  grid-column: 1;
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--fh-subtle);
}

.notif-headline {
  grid-column: 1 / -1;
  color: var(--fh-ink);
  font-size: var(--fh-text-body-md);
  line-height: 1.4;
}

.notif-time {
  grid-column: 2;
  grid-row: 1;
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
  white-space: nowrap;
}
</style>
