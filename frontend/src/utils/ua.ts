/**
 * Tiny User-Agent heuristic - render "Chrome · Windows" instead of a full
 * UA string. Shared by the account session list and the admin session table.
 */
export function uaShort(ua: string | null, fallback = ''): string {
  if (!ua) return fallback
  const br = /Edg\//.test(ua)
    ? 'Edge'
    : /Chrome\//.test(ua)
      ? 'Chrome'
      : /Safari\//.test(ua) && !/Chrome\//.test(ua)
        ? 'Safari'
        : /Firefox\//.test(ua)
          ? 'Firefox'
          : /curl\//.test(ua)
            ? 'curl'
            : /python|httpx/i.test(ua)
              ? 'Python'
              : 'Browser'
  const os = /Windows/.test(ua)
    ? 'Windows'
    : /Mac OS X|Macintosh/.test(ua)
      ? 'macOS'
      : /Linux/.test(ua)
        ? 'Linux'
        : /Android/.test(ua)
          ? 'Android'
          : /iPhone|iPad/.test(ua)
            ? 'iOS'
            : ''
  return os ? `${br} · ${os}` : br
}
