/* No frontend test may make a real HTTP request.
 *
 * happy-dom performs XHR and fetch for real, against its default origin
 * http://localhost:3000 where nothing listens. An unmocked API call therefore
 * does not fail its test: the code under test usually catches the connection
 * error, the test stays green, and the run prints an ECONNREFUSED trace -
 * useUpload's retry test did exactly that under a comment saying it did not
 * touch the network. Here every attempt is refused before it leaves the process
 * and fails the test that made it (from the afterEach below - the code under
 * test may well swallow the failed request). Mock the API module (`vi.mock('@/api/...')`)
 * or give axios an adapter instead (tests/api/client*.test.ts do).
 *
 * axios uses the XHR adapter in this environment; `fetch` is covered for code
 * that calls it directly. EventSource does not exist in happy-dom, so the tests
 * that need it stub it already. */
import { afterEach } from 'vitest'

const attempts: string[] = []

/** Hand over, and clear, the requests refused so far. */
function takeNetworkAttempts(): string[] {
  return attempts.splice(0)
}

// The guard's own test reads this handle instead of importing the module, so
// it also proves vitest.config.ts installs the guard for every test file: an
// import would install it by itself.
export interface NoRealNetworkGuard {
  take: () => string[]
}
;(globalThis as { __noRealNetwork?: NoRealNetworkGuard }).__noRealNetwork = {
  take: takeNetworkAttempts,
}

function refuse(what: string): Error {
  attempts.push(what)
  return new Error(`[test] real network request refused: ${what}`)
}

// open() only records the target (it sends nothing); send() is refused, and the
// request fails the way an unreachable server's does - asynchronously, through
// the error handler - so the code under test sees an ordinary network error
// (axios: ERR_NETWORK, with its config) and behaves as it would for real. A
// microtask, not a timer, so tests on fake timers still see the failure.
const targets = new WeakMap<XMLHttpRequest, string>()
const realOpen = XMLHttpRequest.prototype.open as (
  this: XMLHttpRequest,
  method: string,
  url: string | URL,
  ...rest: unknown[]
) => void
XMLHttpRequest.prototype.open = function (
  this: XMLHttpRequest,
  method: string,
  url: string | URL,
  ...rest: unknown[]
): void {
  targets.set(this, `${method.toUpperCase()} ${String(url)}`)
  realOpen.call(this, method, url, ...rest)
} as XMLHttpRequest['open']
XMLHttpRequest.prototype.send = function (this: XMLHttpRequest): void {
  const { message } = refuse(targets.get(this) ?? 'XHR to an unknown target')
  queueMicrotask(() => {
    this.onerror?.call(this, { type: 'error', message } as unknown as ProgressEvent)
    this.onloadend?.call(this, { type: 'loadend' } as unknown as ProgressEvent)
  })
}

globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  const isRequest = input instanceof Request
  const method = init?.method ?? (isRequest ? input.method : 'GET')
  const url = isRequest ? input.url : String(input)
  return Promise.reject(refuse(`${method.toUpperCase()} ${url}`))
}) as typeof fetch

afterEach(() => {
  const made = takeNetworkAttempts()
  if (made.length) {
    throw new Error(
      `a test made a real network request (refused): ${made.join(', ')}. ` +
        'Mock the API module or give axios an adapter. A request that ran late ' +
        'can belong to an earlier test in the same file.',
    )
  }
})
