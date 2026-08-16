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
 * the stream - we cooperate with that by emitting `id:` in the
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
// After giving up, keep trying at a LOW rate rather than never again.
//
// Five failures at the backoff above is ~22 seconds - shorter than a routine
// in-app Update - so every open, focused tab lost live notifications until the
// user reloaded or switched away and back, with no error, no "disconnected"
// state and no retry control. The rationale for the hard cap was a fail2ban
// jail at 10 requests/hour, which one attempt a minute at a single endpoint
// does not approach; and the `givenUp` flag stays set so the UI can still say
// so (audit #2).
const RETRY_AFTER_GIVEUP_MS = 60_000

export function useSSE(opts: UseSSEOptions) {
  const connected = ref(false)
  const givenUp = ref(false)
  const lastEventId = ref<string | null>(null)
  let es: EventSource | null = null
  let reconnectTimer: number | null = null
  let reconnectAttempt = 0
  let consecutiveErrors = 0
  let stopped = false
  // True between start() and the EventSource actually existing - see _connect.
  let connecting = false

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
    // NOT gated on `givenUp`: the slow retry above deliberately runs after it
    // is set, so the flag means "degraded, telling the user" rather than
    // "dead forever". `stopped` is still absolute.
    if (stopped) return
    givenUp.value = false
    // Set BEFORE the first await: the URL factory is async (it mints a signed
    // token), so `es` stays null across that await and three synchronous
    // start() calls would each get past an `es === null` check and open their
    // own EventSource.
    connecting = true
    let u: string
    try {
      u = await _resolveUrl()
    } catch {
      connecting = false
      // URL factory rejected (e.g. token mint failed because the user
      // logged out). Treat as a soft error and back off - but count
      // toward the give-up budget so a permanently-broken token route
      // doesn't loop indefinitely.
      _onFailure()
      return
    }
    connecting = false
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
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      if (!stopped) {
        reconnectTimer = window.setTimeout(() => {
          reconnectTimer = null
          consecutiveErrors = 0
          reconnectAttempt = 0
          _connect()
        }, RETRY_AFTER_GIVEUP_MS)
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
    // Idempotent. `start()` used to open a SECOND EventSource whenever it was
    // called while one was already live - and NotificationBell called it twice
    // on every mount (an immediate watcher plus an onMounted), so every bell
    // burned two of the five per-user stream slots the server allows, doubled
    // the reconnect traffic, and delivered every notification twice (audit
    // 2026-07-30, fe-correct-1). The visibilitychange handler is a third
    // caller, which is exactly the shape this guard is for.
    if ((es !== null || connecting) && !stopped) return
    // A pending reconnect is exactly the state the guard above lets through:
    // `onerror` closes the stream, nulls `es` and SCHEDULES a retry, and
    // NotificationBell's visibilitychange handler calls start() precisely
    // when `!connected`. Without this the timer then fires a second
    // `_connect()` that overwrites `es`, orphaning the first EventSource with
    // its connection open - unclosable, since stop() can only reach the
    // current one. Duplicate notifications and a burned stream slot for the
    // tab's lifetime, which is the very thing the guard exists to prevent.
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    stopped = false
    givenUp.value = false
    reconnectAttempt = 0
    consecutiveErrors = 0
    void _connect()
  }

  function stop() {
    stopped = true
    connecting = false
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
