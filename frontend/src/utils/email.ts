/** A shape check for an address someone typed, not a validator - the server
 *  is the authority. One copy (the recipient picker and the error-alert
 *  recipients each had their own, audit 2026-11-08). */
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
