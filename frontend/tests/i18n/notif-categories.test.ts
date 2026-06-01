import { describe, expect, it } from 'vitest'

import de from '@/i18n/locales/de.json'
import en from '@/i18n/locales/en.json'

// Mirror of the backend NotificationCategory enum
// (backend/app/models/notification.py). Every value is rendered via
// t(`notif_bell.cat.${category}`) in NotificationItem.vue and
// NotificationPreferences.vue, so a missing key shows the raw key path in
// the bell + the preferences table. Keep this list in sync with the enum.
const CATEGORIES = [
  'share_created',
  'share_expiring',
  'public_link_downloaded',
  'account_created',
  'reset_password',
  'login_alert',
  'oidc_linked',
  'file_quarantined',
  'session_evicted',
  'ops_alert',
  'release_available',
] as const

describe('notif_bell.cat covers every NotificationCategory', () => {
  for (const [name, msgs] of [
    ['en', en],
    ['de', de],
  ] as const) {
    it(`${name} has a string label for every category`, () => {
      const cat = (msgs as Record<string, any>).notif_bell.cat as Record<string, unknown>
      const missing = CATEGORIES.filter((c) => typeof cat[c] !== 'string' || !cat[c])
      expect(missing).toEqual([])
    })
  }
})
