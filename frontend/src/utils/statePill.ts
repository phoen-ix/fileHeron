import type { SecretRequestState, SecretState, ShareState } from '@/types/api'

type PillTone = 'active' | 'warn' | 'danger' | undefined

/**
 * Map a share state to a design-system pill tone. Shared by the share lists +
 * detail (was duplicated as `pillForState` / `pillForShareState`).
 * File-state pills are intentionally NOT unified here - they differ by context
 * (e.g. ready_unscanned reads as active in the recipient view but warn in the
 * admin inventory).
 */
/** Mail-log row status → pill tone. Shared by the mail log and its detail
 *  view, which each carried a byte-identical copy. */
export function mailStatusPill(status: string): PillTone {
  if (status === 'sent') return 'active'
  if (status === 'queued') return 'warn'
  if (status === 'failed' || status === 'error') return 'danger'
  return undefined
}

export function shareStatePill(state: ShareState | string): PillTone {
  if (state === 'active') return 'active'
  if (state === 'expired' || state === 'pending_approval') return 'warn'
  if (state === 'revoked' || state === 'deleted' || state === 'failed' || state === 'rejected')
    return 'danger'
  return undefined
}

/** Secret state (v2.24.0) → pill tone. `burned` is the normal end - every view
 *  was used - so it reads neutral; an unread expiry is a warning, a secret
 *  burned early by hand is the exceptional one. */
export function secretStatePill(state: SecretState | string): PillTone {
  if (state === 'active') return 'active'
  if (state === 'expired') return 'warn'
  if (state === 'revoked') return 'danger'
  return undefined
}

/** What a secret request's pill says: its state, or "expired" once it is past
 *  its time even before the sweep has caught up (`closed_reason`). */
export function secretRequestStateKey(r: {
  state: SecretRequestState
  closed_reason: string | null
}): SecretRequestState {
  return r.state === 'open' && r.closed_reason === 'expired' ? 'expired' : r.state
}

/** Secret request state (v2.24.0) → pill tone. Answered is the normal end. */
export function secretRequestPill(state: SecretRequestState | string): PillTone {
  if (state === 'open') return 'active'
  if (state === 'expired') return 'warn'
  if (state === 'cancelled') return 'danger'
  return undefined
}
