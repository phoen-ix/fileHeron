import type { SecretRecipientSummary } from '@/types/api'

type Translate = (key: string, named?: Record<string, unknown>, plural?: number) => string

/** "2 people · 1 group · link" - who a secret or request went to. Four list
 *  views carried this function line for line (audit 2026-11-08). */
export function secretAudience(
  t: Translate,
  sum: SecretRecipientSummary | null | undefined,
): string {
  if (!sum) return '-'
  const parts: string[] = []
  if (sum.users) parts.push(t('secrets.audience.users', { n: sum.users }, sum.users))
  if (sum.groups) parts.push(t('secrets.audience.groups', { n: sum.groups }, sum.groups))
  if (sum.emails) parts.push(t('secrets.audience.emails', { n: sum.emails }, sum.emails))
  if (sum.link) parts.push(t('secrets.audience.link'))
  return parts.join(' · ') || '-'
}
