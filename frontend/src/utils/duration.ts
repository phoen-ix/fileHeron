/* A share expiry that has not started yet ("1 hour after the files are ready")
 * is stored as a duration in seconds. Render it with the same words the preset
 * buttons use, so the share page repeats what the sender clicked. */

type Translate = (key: string, named?: Record<string, unknown>, plural?: number) => string

const HOUR = 3600
const DAY = 24 * HOUR

const PRESET_SECONDS: Record<number, string> = {
  [HOUR]: '1h',
  [DAY]: '1d',
  [7 * DAY]: '7d',
  [14 * DAY]: '14d',
  [30 * DAY]: '30d',
  [60 * DAY]: '60d',
  [90 * DAY]: '90d',
  [365 * DAY]: '1y',
}

export function durationLabel(seconds: number, t: Translate): string {
  const preset = PRESET_SECONDS[seconds]
  if (preset) return t(`expiry.presets.${preset}`)
  if (seconds % DAY === 0) {
    const n = seconds / DAY
    return t('expiry.duration_days', { n }, n)
  }
  const n = Math.max(1, Math.round(seconds / HOUR))
  return t('expiry.duration_hours', { n }, n)
}

/** "1 hour after the files are ready" - the share-page wording for a preset
 *  expiry whose clock has not started. */
export function afterReadyLabel(seconds: number, t: Translate): string {
  return t('expiry.after_ready', { duration: durationLabel(seconds, t) })
}
