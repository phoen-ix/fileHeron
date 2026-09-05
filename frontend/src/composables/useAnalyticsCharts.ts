/* Tiny pure SVG-geometry helpers for the analytics dashboard. No chart lib -
 * we hand-roll <polyline>/<rect> against a fixed viewBox and style with
 * --fh-* tokens, matching the "no UI framework" rule. All functions are pure
 * (geometry only) so they're trivially unit-testable. */

interface Bar {
  x: number
  y: number
  width: number
  height: number
}

/** Map values to evenly-spaced bars filling `width` × `height`. Heights scale
 *  to the max value (0 → a 0-height bar). `gap` is the fraction (0-1) of each
 *  slot left as spacing. */
export function scaleBars(
  values: number[],
  width: number,
  height: number,
  gap = 0.25,
): Bar[] {
  if (values.length === 0) return []
  const max = Math.max(1, ...values)
  const slot = width / values.length
  const barW = slot * (1 - gap)
  return values.map((v, i) => {
    const h = max > 0 ? (Math.max(0, v) / max) * height : 0
    return {
      x: i * slot + (slot - barW) / 2,
      y: height - h,
      width: barW,
      height: h,
    }
  })
}

/** Map values to an SVG polyline `points` string across `width` × `height`,
 *  scaled between the series min and max (flat series → a centred line). */
export function linePoints(values: number[], width: number, height: number): string {
  if (values.length === 0) return ''
  if (values.length === 1) {
    return `0,${height / 2} ${width},${height / 2}`
  }
  const max = Math.max(...values)
  const min = Math.min(...values)
  const span = max - min || 1
  const stepX = width / (values.length - 1)
  return values
    .map((v, i) => {
      const y = height - ((v - min) / span) * height
      return `${(i * stepX).toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')
}
