/**
 * Where to go after an SSO round-trip.
 *
 * The OIDC callback is a backend redirect and always lands on `/`, so the
 * guard's `?redirect=` deep link - which the password form honours - was lost
 * for every SSO sign-in (audit 2026-07-30, fe-auth-7). Login.vue stashes the
 * target before navigating to the IdP; the router consumes it once on the way
 * back.
 *
 * sessionStorage so it dies with the tab, and same-origin paths only so the
 * stored value can never become an open redirect.
 */
export const POST_LOGIN_REDIRECT_KEY = 'fh_post_login_redirect'

export function takePostLoginRedirect(): string | null {
  try {
    const raw = window.sessionStorage?.getItem(POST_LOGIN_REDIRECT_KEY)
    window.sessionStorage?.removeItem(POST_LOGIN_REDIRECT_KEY)
    if (!raw || !raw.startsWith('/') || raw.startsWith('//')) return null
    return raw
  } catch {
    return null
  }
}
