/* "Operational alerts" was one row for sixteen different alarms and notices,
 * so an admin could not say "mail me when antivirus fails, not when an update
 * starts". The server now sends one item per alert type (`topic`, with the
 * `group` it is listed under); a change is keyed `<category>:<topic>`. */
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import en from '@/i18n/locales/en.json'
import NotificationPreferencesTable from '@/components/NotificationPreferencesTable.vue'
import type { PreferenceItem } from '@/types/api'

const ITEMS: PreferenceItem[] = [
  { category: 'share_created', channel: 'both', locked: false, one_click: true },
  { category: 'login_alert', channel: 'email', locked: true, one_click: true },
  { category: 'ops_alert', topic: 'av_scan_failed', group: 'antivirus', channel: 'both', locked: false, one_click: false },
  { category: 'ops_alert', topic: 'av_unhealthy', group: 'antivirus', channel: 'both', locked: false, one_click: false },
  { category: 'ops_alert', topic: 'update_completed', group: 'updates', channel: 'in_app', locked: false, one_click: false },
  { category: 'release_available', channel: 'both', locked: false, one_click: true },
]

function mountTable(items = ITEMS) {
  const i18n = createI18n({ legacy: false, locale: 'en', fallbackLocale: 'en', messages: { en } })
  return mount(NotificationPreferencesTable, { props: { items }, global: { plugins: [i18n] } })
}

describe('NotificationPreferencesTable', () => {
  it('lists each alert type on its own row, under its group', () => {
    const w = mountTable()
    const rows = w.findAll('tbody tr').map((r) => r.text().replace(/\s+/g, ' ').trim())

    const headings = w.findAll('tbody tr.group-row').map((r) => r.text().trim())
    expect(headings).toEqual([
      `${en.notif_prefs.group_heading} · ${en.notif_prefs.group.antivirus}`,
      `${en.notif_prefs.group_heading} · ${en.notif_prefs.group.updates}`,
    ])
    // A heading opens each group once, in the order the server sent the items.
    expect(rows.findIndex((r) => r.startsWith(headings[0]))).toBe(2)
    expect(w.findAll('select')).toHaveLength(ITEMS.length)

    const labels = w.findAll('tbody tr:not(.group-row) .cat-cell').map((c) => c.text())
    expect(labels).toContain(en.notif_prefs.topic.ops_alert.av_scan_failed)
    expect(labels).toContain(en.notif_prefs.topic.ops_alert.update_completed)
    // Not sixteen rows all called "Ops alert".
    expect(labels.filter((l) => l === en.notif_bell.cat.ops_alert)).toEqual([])
  })

  it('names the alert type in each select, for a screen reader', () => {
    const w = mountTable()
    const names = w.findAll('select').map((s) => s.attributes('aria-label'))
    expect(new Set(names).size).toBe(names.length)
    expect(names).toContain(
      en.notif_prefs.channel_for.replace('{category}', en.notif_prefs.topic.ops_alert.av_unhealthy),
    )
  })

  it('reports a change by category, or by category and alert type', async () => {
    const w = mountTable()
    const selects = w.findAll('select')
    await selects[0].setValue('email')
    await selects[3].setValue('off')
    expect(w.emitted('change')).toEqual([
      ['share_created', 'email'],
      ['ops_alert:av_unhealthy', 'off'],
    ])
  })

  it('shows an alert type the SPA has no words for by its id, never blank', () => {
    const w = mountTable([
      { category: 'ops_alert', topic: 'brand_new_alarm', group: 'antivirus', channel: 'both', locked: false, one_click: false },
    ])
    expect(w.find('tbody tr:not(.group-row) .cat-cell').text()).toBe('brand_new_alarm')
  })
})
