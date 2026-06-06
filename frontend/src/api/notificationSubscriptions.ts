/* Anonymous, token-authed subscription management (the email "Manage
 * subscriptions" / "Unsubscribe" links). Mirrors api/publicLinks.ts: a
 * standalone axios instance with NO auth interceptor, so a logged-out
 * recipient isn't bounced to /login. The signed token in the URL is the auth. */
import axios from 'axios'

import type {
  NotificationChannel,
  SubscriptionContextResponse,
  UnsubscribeResponse,
} from '@/types/api'

const subClient = axios.create({
  baseURL: '/',
  withCredentials: true,
})

export function fetchSubscriptions(token: string) {
  return subClient.get<SubscriptionContextResponse>(
    `/api/notification-subscriptions/${token}`,
  )
}

export function updateSubscriptions(
  token: string,
  preferences: Record<string, NotificationChannel>,
) {
  return subClient.put<SubscriptionContextResponse>(
    `/api/notification-subscriptions/${token}`,
    { preferences },
  )
}

export function unsubscribeCategory(token: string, category: string) {
  return subClient.post<UnsubscribeResponse>(
    `/api/notification-subscriptions/${token}/unsubscribe`,
    { category },
  )
}
