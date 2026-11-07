/* Anonymous, token-authed subscription management (the email "Manage
 * subscriptions" / "Unsubscribe" links). Mirrors api/publicLinks.ts: a
 * bare client with NO auth interceptor, so a logged-out
 * recipient isn't bounced to /login. The signed token in the URL is the auth. */
import { createBareClient } from './client'

import type {
  NotificationChannel,
  SubscriptionContextResponse,
  UnsubscribeResponse,
} from '@/types/api'

const subClient = createBareClient('/', { withCredentials: true })

export function fetchSubscriptions(token: string) {
  return subClient.get<SubscriptionContextResponse>(`/api/notification-subscriptions/${token}`)
}

export function updateSubscriptions(
  token: string,
  preferences: Record<string, NotificationChannel>,
) {
  return subClient.put<SubscriptionContextResponse>(`/api/notification-subscriptions/${token}`, {
    preferences,
  })
}

export function unsubscribeCategory(token: string, category: string) {
  return subClient.post<UnsubscribeResponse>(
    `/api/notification-subscriptions/${token}/unsubscribe`,
    { category },
  )
}
