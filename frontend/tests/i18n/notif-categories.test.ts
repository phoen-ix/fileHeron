import { describe, expect, it } from 'vitest'

import de from '@/i18n/locales/de.json'
import en from '@/i18n/locales/en.json'

// Mirror of the backend NotificationCategory enum
// (backend/app/models/notification.py). Every value is rendered via
// t(`notif_bell.cat.${category}`) in NotificationItem.vue and
// NotificationPreferences.vue, so a missing key shows the raw key path in
// the bell + the preferences table.
//
// "Keep this list in sync with the enum" is the defect, not the instruction: a
// list you have to remember to update cannot catch the thing you forgot. It sat
// one value stale (`server_error`, the most recently added category) for
// exactly that reason. The TOKEN_SCOPES block below is the shape that does not
// rot — it imports the source of truth — and it is not hypothetical: it fails
// on a real missing label the moment one is added without its translation.
const CATEGORIES = [
  'share_created',
  'share_files_added',
  'share_expiring',
  'share_pending_approval',
  'share_approved',
  'share_rejected',
  'public_link_downloaded',
  'account_created',
  'reset_password',
  'login_alert',
  'oidc_linked',
  'file_quarantined',
  'session_evicted',
  'ops_alert',
  'release_available',
  'inbound_message',
  'server_error',
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


// The importable version of the same idea, over a list the frontend itself
// owns. This is what would have caught `public_links:read` shipping as a
// checkbox labelled `api_tokens.scopes.public_links_read` in BOTH locales -
// the en/de parity test could not, because the key was missing from both.
describe('api_tokens.scopes covers every TOKEN_SCOPE', () => {
  for (const [name, msgs] of [
    ['en', en],
    ['de', de],
  ] as const) {
    it(`${name} labels every scope the picker renders`, async () => {
      const { TOKEN_SCOPES, scopeLabelKey } = await import('@/utils/tokenScopes')
      const missing: string[] = []
      for (const scope of TOKEN_SCOPES) {
        const key = scopeLabelKey(scope)
        const leaf = key
          .split('.')
          .reduce<unknown>((acc, part) => (acc as Record<string, unknown>)?.[part], msgs)
        if (typeof leaf !== 'string' || leaf.length === 0) missing.push(scope)
      }
      expect(missing, `unlabelled scopes render their raw key path`).toEqual([])
    })
  }
})
