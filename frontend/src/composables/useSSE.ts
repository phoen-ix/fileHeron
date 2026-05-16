/* Minimal EventSource wrapper with auto-reconnect, Last-Event-Id, and
 * a per-connect URL factory.
 *
 * Why hand-rolled (not @vueuse/integrations): the deps surface stays
 * small and the reconnect-with-Last-Event-Id behavior is specific
 * enough that wrapping a generic helper would obscure rather than
 * clarify the lifecycle.
 *
 * The browser's EventSource attaches the Last-Event-Id header on
 * reconnect automatically when an `id:` field has been observed in
 * the stream — we cooperate with that by emitting `id:` in the
 * server's frames (see services/sse.py). On a manual reconnect we
 * pass the last seen id explicitly via a query param so the server
 * can catch up.
 *
 * URL factory: callers pass a function `(lastEventId) => Promise<string>`
 * resolved on every connect. Used by NotificationBell to mint a fresh
 * signed SSE token (EventSource can't send Authorization headers, so
 * auth rides in the URL). On error the timer backs off exponentially
 * with a cap so a token-mint failure can't loop the browser.
 *
 * Hard cap on consecutive failures: EventSource doesn't expose the
 * HTTP status to onerror, so a persistent 401 (rotated key across a
 * deploy, role downgrade, broken stream-token route) is
 * indistinguishable from a transient network blip and would otherwise
 * retry forever. After MAX_CONSECUTIVE_ERRORS reconnect attempts
 * without a single successful `onopen`, we surface `givenUp = true`
 * and stop. Callers can offer a manual `restart()` button. Real-world
 * incident 2026-05-16: such a loop produced ~120 401s/hour on
 * /api/admin/system/stream which tripped the host's fail2ban
 * traefik-auth jail (10/hour) and banned the admin's IP for 24h.
 */
import { onBeforeUnmount, ref } from 'vue'

export type SSEUrlFactory = (lastEventId: string | null) => string | Promise<string>

export interface UseSSEOptions {
  url: string | SSEUrlFactory
  onMessage: (event: MessageEvent) => void
  onOpen?: () => void
  onError?: (e: Event) => void
}

const RECONNECT_BASE_MS = 1500
const RECONNECT_CAP_MS = 30_000
const MAX_CONSECUTIVE_ERRORS = 5

export function useSSE(opts: UseSSEOptions) {
  const connected = ref(false)
  const givenUp = ref(false)
  const lastEventId = ref<string | null>(null)
  let es: EventSource | null = null
  let reconnectTimer: number | null = null
  let reconnectAttempt = 0
  let consecutiveErrors = 0
  let stopped = false

  function _appendLastId(url: string): string {
    if (!lastEventId.value) return url
    const sep = url.includes('?') ? '&' : '?'
    return `${url}${sep}last_event_id=${encodeURIComponent(lastEventId.value)}`
  }

  async function _resolveUrl(): Promise<string> {
    if (typeof opts.url === 'function') {
      // Factory is responsible for whatever auth params it wants;
      // we still tack on last_event_id so server-side catch-up works.
      const base = await opts.url(lastEventId.value)
      return _appendLastId(base)
    }
    return _appendLastId(opts.url)
  }

  async function _connect() {
    if (stopped || givenUp.value) return
    let u: string
    try {
      u = await _resolveUrl()
    } catch {
      // URL factory rejected (e.g. token mint failed because the user
      // logged out). Treat as a soft error and back off — but count
      // toward the give-up budget so a permanently-broken token route
      // doesn't loop indefinitely.
      _onFailure()
      return
    }
    es = new EventSource(u, { withCredentials: true })
    es.onopen = () => {
      connected.value = true
      reconnectAttempt = 0  // reset backoff on successful open
      consecutiveErrors = 0
      opts.onOpen?.()
    }
    es.onmessage = (e) => {
      if (e.lastEventId) lastEventId.value = e.lastEventId
      opts.onMessage(e)
    }
    // Custom event types: forward as MessageEvent.
    es.addEventListener('notification', (e) => {
      const me = e as MessageEvent
      if (me.lastEventId) lastEventId.value = me.lastEventId
      opts.onMessage(me)
    })
    es.onerror = (e) => {
      connected.value = false
      opts.onError?.(e)
      es?.close()
      es = null
      _onFailure()
    }
  }

  function _onFailure() {
    consecutiveErrors += 1
    if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
      givenUp.value = true
      stopped = true
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      return
    }
    _scheduleReconnect()
  }

  function _scheduleReconnect() {
    if (stopped || reconnectTimer !== null) return
    const delay = Math.min(
      RECONNECT_CAP_MS,
      RECONNECT_BASE_MS * 2 ** Math.min(reconnectAttempt, 5),
    )
    reconnectAttempt += 1
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null
      void _connect()
    }, delay)
  }

  function start() {
    stopped = false
    givenUp.value = false
    reconnectAttempt = 0
    consecutiveErrors = 0
    void _connect()
  }

  function stop() {
    stopped = true
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    es?.close()
    es = null
    connected.value = false
  }

  onBeforeUnmount(stop)

  return { connected, givenUp, lastEventId, start, stop }
}
