/* Password strength heuristic. Length-biased per NIST 800-63B
 * direction. Score 0–3 maps to weak / fair / good / strong; UI renders
 * four bars. Real defense is server-side: min length, HIBP k-anonymity
 * breach lookup on every change, and lockout. This meter is a UX hint. */

import { computed, type Ref } from 'vue'

export type StrengthLabel = 'weak' | 'fair' | 'good' | 'strong'

export interface Strength {
  score: 0 | 1 | 2 | 3
  label: StrengthLabel
}

function classCount(pw: string): number {
  let c = 0
  if (/[a-z]/.test(pw)) c++
  if (/[A-Z]/.test(pw)) c++
  if (/\d/.test(pw)) c++
  if (/[^A-Za-z0-9]/.test(pw)) c++
  return c
}

export function scorePassword(pw: string): Strength {
  if (!pw) return { score: 0, label: 'weak' }
  const len = pw.length
  const classes = classCount(pw)

  // Heuristic — biased towards length over complexity, matches NIST 800-63B
  // direction. Min length 12 enforced server-side; we score progressively.
  if (len < 8) return { score: 0, label: 'weak' }
  if (len < 12) return { score: classes >= 3 ? 1 : 0, label: classes >= 3 ? 'fair' : 'weak' }
  if (len < 16) return { score: classes >= 3 ? 2 : 1, label: classes >= 3 ? 'good' : 'fair' }
  return { score: classes >= 2 ? 3 : 2, label: classes >= 2 ? 'strong' : 'good' }
}

export function usePasswordStrength(pw: Ref<string>) {
  return computed<Strength>(() => scorePassword(pw.value))
}
