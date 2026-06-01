import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useSiteStore } from '@/stores/site'
import {
  formatDateInSiteTime,
  formatInSiteTime,
  parseServerDate,
  siteLocalIsoToUtcIso,
  siteNowPlusIso,
} from '@/utils/datetime'

describe('parseServerDate', () => {
  it('appends Z to a naive ISO so the value is treated as UTC', () => {
    // 2026-05-16 23:46:07 UTC = 1779580_secs since epoch (approx).
    // What matters: the same wall-clock string parses to the same Date.
    const naive = parseServerDate('2026-05-16T23:46:07')
    const withZ = parseServerDate('2026-05-16T23:46:07Z')
    expect(naive.getTime()).toBe(withZ.getTime())
  })

  it('leaves a string with an explicit Z designator alone', () => {
    const d = parseServerDate('2026-05-16T23:46:07Z')
    expect(d.getUTCHours()).toBe(23)
    expect(d.getUTCMinutes()).toBe(46)
  })

  it('leaves a string with a numeric offset alone', () => {
    const d = parseServerDate('2026-05-16T23:46:07+02:00')
    // 23:46 in +02:00 == 21:46 UTC
    expect(d.getUTCHours()).toBe(21)
    expect(d.getUTCMinutes()).toBe(46)
  })
})

describe('formatInSiteTime', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('returns the em-dash placeholder for null / undefined / empty', () => {
    expect(formatInSiteTime(null, 'en')).toBe('—')
    expect(formatInSiteTime(undefined, 'en')).toBe('—')
    expect(formatInSiteTime('', 'en')).toBe('—')
  })

  it('renders the same instant differently for UTC vs Europe/Vienna vs Pacific/Auckland', () => {
    const site = useSiteStore()
    const iso = '2026-05-16T23:46:00' // naive UTC

    site.timezone = 'UTC'
    const utc = formatInSiteTime(iso, 'en')

    site.timezone = 'Europe/Vienna' // CEST = +02:00 in May
    const vie = formatInSiteTime(iso, 'en')

    site.timezone = 'Pacific/Auckland' // NZST = +12:00 in May
    const nzl = formatInSiteTime(iso, 'en')

    // The three formatted strings should all differ from each other.
    expect(utc).not.toBe(vie)
    expect(vie).not.toBe(nzl)
    expect(utc).not.toBe(nzl)
  })

  it('reflects locale on the format-language axis (en vs de)', () => {
    const site = useSiteStore()
    site.timezone = 'UTC'
    const en = formatInSiteTime('2026-05-16T23:46:00', 'en')
    const de = formatInSiteTime('2026-05-16T23:46:00', 'de')
    // de-AT vs en-US format the month and separators differently;
    // we don't assert exact substrings (CLDR may change) but they
    // must not be byte-identical.
    expect(en).not.toBe(de)
  })

  it('falls back to UTC when the store has no timezone set', () => {
    const site = useSiteStore()
    site.timezone = '' // simulate pre-bootstrap default
    const out = formatInSiteTime('2026-05-16T23:46:00', 'en')
    expect(out).toMatch(/2026/) // smoke — should produce some output
  })

  it('renders 24-hour with a zone label in the site timezone (not 12-hour, not UTC)', () => {
    const site = useSiteStore()
    site.timezone = 'Europe/Vienna' // CEST = +02:00 in May
    // 11:00 UTC -> 13:00 Vienna. Must be 24-hour ("13:00", no AM/PM) and
    // carry a DST-aware zone token so it can't be mistaken for UTC.
    const out = formatInSiteTime('2026-05-31T11:00:00', 'en')
    expect(out).toContain('13:00')
    expect(out).not.toMatch(/\b(AM|PM)\b/)
    expect(out).toMatch(/GMT|CEST|CET/)
  })

  it('labels a UTC site timezone as UTC', () => {
    const site = useSiteStore()
    site.timezone = 'UTC'
    const out = formatInSiteTime('2026-05-31T11:00:00', 'en')
    expect(out).toContain('11:00')
    expect(out).toContain('UTC')
  })
})

describe('formatDateInSiteTime', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('omits hour and minute', () => {
    const site = useSiteStore()
    site.timezone = 'UTC'
    const out = formatDateInSiteTime('2026-05-16T23:46:00', 'en')
    // The narrow-date format shouldn't include a colon (which is in
    // hh:mm). Date-only shows month + day + year only.
    expect(out).not.toMatch(/:/)
  })

  it('carries no zone token on a date-only cell', () => {
    const site = useSiteStore()
    site.timezone = 'Europe/Vienna'
    const out = formatDateInSiteTime('2026-05-16T23:46:00', 'en')
    expect(out).not.toMatch(/GMT|CEST|CET|UTC/)
  })
})

describe('site-tz expiry pipeline (siteNowPlusIso / siteLocalIsoToUtcIso)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('interprets the picker wall-clock in the site tz, not the browser tz', () => {
    const site = useSiteStore()
    site.timezone = 'Europe/Vienna' // CEST = +02:00 in June
    // 15:10 Vienna == 13:10 UTC — independent of the test runner's TZ.
    expect(siteLocalIsoToUtcIso('2026-06-08T15:10:00')).toBe('2026-06-08T13:10:00.000Z')
  })

  it('is a no-op on the wall-clock when the site tz is UTC', () => {
    const site = useSiteStore()
    site.timezone = 'UTC'
    expect(siteLocalIsoToUtcIso('2026-06-08T15:10:00')).toBe('2026-06-08T15:10:00.000Z')
  })

  it('round-trips now+7d back to the correct instant (no browser-vs-site skew)', () => {
    const site = useSiteStore()
    site.timezone = 'Europe/Vienna'
    const ms = 7 * 24 * 60 * 60 * 1000
    const before = Date.now()
    const utcMs = new Date(siteLocalIsoToUtcIso(siteNowPlusIso(ms))).getTime()
    const after = Date.now()
    // The picker convention drops sub-second precision; allow a small band.
    expect(utcMs).toBeGreaterThanOrEqual(before + ms - 2000)
    expect(utcMs).toBeLessThanOrEqual(after + ms + 2000)
  })
})
