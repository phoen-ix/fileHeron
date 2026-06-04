/**
 * Human-readable byte size — the single source of truth for the ~6 views
 * that previously each defined their own `formatBytes`. Renders e.g.
 * "0 B", "512 B", "1.5 KB", "240 MB", "3.2 GB".
 */
export function formatBytes(n: number | null | undefined): string {
  if (!n || n < 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  // Whole numbers for bytes and for large values (≥100); one decimal otherwise.
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}
