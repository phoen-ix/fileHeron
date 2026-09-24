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
  // Mobile first: Android user agents carry "Linux; Android" and iOS ones
  // "like Mac OS X", so testing Linux/macOS first labelled every phone session
  // "Linux" or "macOS" - in the very lists people read to decide what to revoke.
  const os = /Windows/.test(ua)
    ? 'Windows'
    : /Android/.test(ua)
      ? 'Android'
      : /iPhone|iPad|iPod/.test(ua)
        ? 'iOS'
        : /Mac OS X|Macintosh/.test(ua)
          ? 'macOS'
          : /Linux/.test(ua)
            ? 'Linux'
            : ''
  return os ? `${br} · ${os}` : br
}
