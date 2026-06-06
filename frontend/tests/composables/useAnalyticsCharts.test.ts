import { describe, expect, it } from 'vitest'

import { linePoints, scaleBars } from '@/composables/useAnalyticsCharts'

describe('scaleBars', () => {
  it('returns one bar per value, tallest at the max', () => {
    const bars = scaleBars([0, 5, 10], 600, 120, 0)
    expect(bars).toHaveLength(3)
    expect(bars[0].height).toBe(0) // zero value → zero height
    expect(bars[2].height).toBe(120) // max value → full height
    expect(bars[1].height).toBeCloseTo(60) // half → half
    // bars sit on the baseline (y + height == chart height)
    bars.forEach((b) => expect(b.y + b.height).toBeCloseTo(120))
  })

  it('handles an empty series', () => {
    expect(scaleBars([], 600, 120)).toEqual([])
  })
})

describe('linePoints', () => {
  it('maps min→bottom and max→top across the width', () => {
    const pts = linePoints([0, 10], 600, 120).split(' ')
    expect(pts).toHaveLength(2)
    expect(pts[0]).toBe('0.00,120.00') // first point, min → bottom
    expect(pts[1]).toBe('600.00,0.00') // last point, max → top
  })

  it('centres a single point', () => {
    expect(linePoints([42], 600, 120)).toBe('0,60 600,60')
  })

  it('handles an empty series', () => {
    expect(linePoints([], 600, 120)).toBe('')
  })
})
