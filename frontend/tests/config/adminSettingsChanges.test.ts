/* config/adminSettingsChanges.ts: every page it links a settings change to is a
 * real admin route, and the link resolves the way the Overview panel needs. The
 * backend half (test_settings_change_links_pin.py) pins that every settings
 * audit's target id is in the map. */
import { describe, expect, it } from 'vitest'

import { ADMIN_ROUTE_NAMES } from '@/config/adminNav'
import {
  EVENT_ROUTES,
  SETTINGS_TARGET_ROUTES,
  settingsChangeDetail,
  settingsChangeLink,
} from '@/config/adminSettingsChanges'
import type { AdminAuditRow } from '@/types/api'

function row(over: Partial<AdminAuditRow>): AdminAuditRow {
  return {
    id: 1, event_type: 'settings_changed', actor_user_id: null, actor_display_name: null,
    actor_email: null, target_type: 'settings', target_id: null, request_id: null, ip: null,
    extra: null, created_at: '2026-10-02T10:00:00', ...over,
  }
}

describe('adminSettingsChanges', () => {
  it('links only to real admin routes', () => {
    const routes = [...Object.values(SETTINGS_TARGET_ROUTES), ...Object.values(EVENT_ROUTES)]
    expect(routes.length).toBeGreaterThan(20)
    for (const r of routes) expect(ADMIN_ROUTE_NAMES.has(r), r).toBe(true)
  })

  it('places a change by its target, or by its event where that decides', () => {
    expect(settingsChangeLink(row({ event_type: 'legal_changed', target_id: 'legal' }))).toEqual({
      routeName: 'admin-settings-legal',
    })
    expect(
      settingsChangeLink(row({ event_type: 'ip_allowlisted', target_id: 'scan_guard' })).routeName,
    ).toBe('admin-ip-blocks')
    expect(
      settingsChangeLink(row({ event_type: 'cron_schedule_changed', target_type: 'cron' })).routeName,
    ).toBe('admin-scheduled-tasks')
  })

  it('places a tunables save through the search index, falling back to its page', () => {
    expect(settingsChangeLink(row({ extra: { keys: ['branding.app_name'] } }))).toEqual({
      routeName: 'admin-settings-branding',
      hash: '#tunable-branding.app_name',
    })
    expect(settingsChangeLink(row({ extra: { keys: ['no.such.key'] } }))).toEqual({
      routeName: 'admin-settings-advanced',
    })
    expect(settingsChangeLink(row({ event_type: 'mystery', target_id: 'x' }))).toEqual({
      routeName: 'admin-audit',
    })
  })

  it('describes what changed without any value', () => {
    expect(settingsChangeDetail(row({ extra: { keys: ['a.b', 'c.d'] } }))).toBe('a.b, c.d')
    expect(
      settingsChangeDetail(row({ event_type: 'email_template_changed', target_id: 'welcome:de' })),
    ).toBe('welcome:de')
    expect(
      settingsChangeDetail(row({ event_type: 'site_url_changed', extra: { from: 'a', to: 'b' } })),
    ).toBeNull()
  })
})
