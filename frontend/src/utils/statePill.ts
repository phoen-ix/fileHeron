import type { ShareState } from '@/types/api'

export type PillTone = 'active' | 'warn' | 'danger' | undefined

/**
 * Map a share state to a design-system pill tone. Shared by the share lists +
 * detail (was duplicated as `pillForState` / `pillForShareState`).
 * File-state pills are intentionally NOT unified here - they differ by context
 * (e.g. ready_unscanned reads as active in the recipient view but warn in the
 * admin inventory).
 */
export function shareStatePill(state: ShareState | string): PillTone {
  if (state === 'active') return 'active'
  if (state === 'expired' || state === 'pending_approval') return 'warn'
  if (
    state === 'revoked' ||
    state === 'deleted' ||
    state === 'failed' ||
    state === 'rejected'
  )
    return 'danger'
  return undefined
}
