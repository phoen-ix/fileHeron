<template>
  <div ref="root" class="bell-wrap">
    <button
      type="button"
      class="bell-trigger"
      :aria-expanded="open"
      :aria-label="t('notif_bell.aria')"
      @click="open = !open"
    >
      <svg
        class="bell-icon"
        viewBox="0 0 16 16"
        width="18"
        height="18"
        fill="none"
        stroke="currentColor"
        stroke-width="1.4"
        aria-hidden="true"
      >
        <path d="M3.5 6.5a4.5 4.5 0 1 1 9 0v2.3l1.4 2.4H2.1l1.4-2.4z" />
        <path d="M6.4 13.2a1.6 1.6 0 0 0 3.2 0" />
      </svg>
      <span v-if="store.unreadCount > 0" class="bell-badge">
        {{ store.unreadCount > 99 ? '99+' : store.unreadCount }}
      </span>
    </button>

    <div v-if="open" class="bell-pop" role="region" :aria-label="t('notif_bell.aria')">
      <div class="bell-head">
        <span class="bell-title">{{ t('notif_bell.title') }}</span>
        <button
          v-if="store.items.length > 0"
          type="button"
          class="fh-btn-text mark-all"
          @click="onDeleteAll"
        >
          {{ t('notif_bell.delete_all') }}
        </button>
      </div>
      <ul v-if="store.items.length" class="bell-list" role="list">
        <NotificationItem
          v-for="item in store.items"
          :key="item.id"
          :item="item"
          @click="onItemClick(item)"
        />
      </ul>
      <p v-else class="bell-empty">{{ t('notif_bell.empty') }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onClickOutside } from '@vueuse/core'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { getStreamToken } from '@/api/notifications'
import { useSSE } from '@/composables/useSSE'
import { useNotificationsStore } from '@/stores/notifications'
import { useAuthStore } from '@/stores/auth'
import type { NotificationItem as NItem } from '@/types/api'

import NotificationItem from './NotificationItem.vue'

const { t } = useI18n()
const router = useRouter()
const auth = useAuthStore()
const store = useNotificationsStore()

const open = ref(false)
const root = ref<HTMLElement | null>(null)

onClickOutside(root, () => (open.value = false))

const sse = useSSE({
  // Mint a fresh signed token on every (re)connect - EventSource can't
  // send Authorization headers, so auth rides on `?token=`. The token's TTL is
  // 5 minutes (services/sse_token.py::DEFAULT_TTL_SEC), comfortably more than
  // the 60s connection cycle so a throttled background tab can still
  // reconnect. This comment said 2 minutes, which was the value before the TTL
  // was raised for exactly that reason - and a stale number here is what a
  // later change would "restore" to (audit 2026-07-30, fe-auth-11).
  async url() {
    const { data } = await getStreamToken()
    return `/api/notifications/stream?token=${encodeURIComponent(data.token)}`
  },
  onMessage(e) {
    try {
      const data = JSON.parse(e.data) as NItem
      store.pushFromSSE(data)
    } catch {
      /* ignore malformed frame */
    }
  },
  onOpen() {
    store.connected = true
  },
  onError() {
    store.connected = false
  },
})

watch(
  () => auth.isAuthenticated,
  (authed) => {
    if (authed) {
      void store.refresh()
      sse.start()
    } else {
      sse.stop()
      store.reset()
    }
  },
  { immediate: true },
)

// No onMounted duplicate of the watcher above: `{ immediate: true }` already
// runs it during setup, so mounting refreshed the bell and opened the stream
// twice (audit 2026-07-30, fe-correct-1).

// Background tabs get throttled, which can stall an SSE reconnect long enough
// for the stream to drop and the composable to give up. On refocus, restart it
// if we're authed but no longer connected so live notifications recover without
// a page reload.
function onVisible() {
  if (
    document.visibilityState === 'visible' &&
    auth.isAuthenticated &&
    !sse.connected.value
  ) {
    sse.start()
  }
}
onMounted(() => document.addEventListener('visibilitychange', onVisible))
onBeforeUnmount(() => document.removeEventListener('visibilitychange', onVisible))

async function onDeleteAll() {
  await store.removeAll()
}

function onItemClick(item: NItem) {
  // Single click dismisses the notification (hard delete) - navigate to its
  // target first, then delete. Deletion is optimistic in the store.
  open.value = false
  navigateToLink(item)
  void store.remove(item.id)
}

function navigateToLink(item: NItem) {
  if (!item.link_url) return

  // Defense-in-depth: only follow same-origin app paths. The
  // backend currently never emits external URLs in notification
  // payloads, but if a future bug ever did, refusing here prevents
  // an open-redirect / XSS-via-navigation. We require:
  // - relative path starting with "/" (not "//" which is
  //   protocol-relative), OR
  // - an absolute URL whose origin matches our own.
  try {
    const raw = item.link_url
    if (raw.startsWith('/') && !raw.startsWith('//')) {
      void router.push(raw)
      return
    }
    const u = new URL(raw, window.location.origin)
    if (u.origin === window.location.origin) {
      void router.push(u.pathname + u.search + u.hash)
      return
    }
  } catch {
    /* malformed URL - refuse to follow */
  }
  // External / suspicious URL → silently ignore (don't navigate).
}
</script>

<style scoped>
.bell-wrap {
  position: relative;
}

.bell-trigger {
  position: relative;
  display: inline-grid;
  place-items: center;
  width: 32px;
  height: 32px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--fh-radius-sm);
  color: var(--fh-ink);
  cursor: pointer;
  transition: border-color var(--fh-duration-fast) var(--fh-easing);
}

.bell-trigger:hover {
  border-color: var(--fh-hairline-strong);
}

.bell-icon {
  display: block;
}

.bell-badge {
  position: absolute;
  top: -2px;
  right: -2px;
  background: var(--fh-accent);
  color: var(--fh-paper);
  font-family: var(--fh-font-mono);
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
  padding: 2px 5px;
  border-radius: 9px;
  border: 1px solid var(--fh-paper);
}

.bell-pop {
  position: absolute;
  right: 0;
  top: calc(100% + var(--fh-space-2));
  width: min(360px, 90vw);
  background: var(--fh-paper-raised);
  border: 1px solid var(--fh-hairline-strong);
  box-shadow: 0 4px 32px rgba(26, 29, 36, 0.06);
  z-index: 30;
}

.bell-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: var(--fh-space-3);
  border-bottom: var(--fh-border);
}

.bell-title {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--fh-subtle);
}

.mark-all {
  font-size: var(--fh-text-body-sm);
}

.bell-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 60vh;
  overflow-y: auto;
}

.bell-empty {
  padding: var(--fh-space-4) var(--fh-space-3);
  color: var(--fh-subtle);
  font-size: var(--fh-text-body-sm);
  text-align: center;
}
</style>
