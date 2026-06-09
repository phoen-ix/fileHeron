/* Anonymous client telemetry. Standalone axios instance (no auth interceptor,
 * like api/legal.ts) - the 404 page reports for logged-out visitors too. */
import axios from 'axios'

const telemetryClient = axios.create({ baseURL: '/' })

/** Report a client-side 404 (a page path Vue Router couldn't match). Best-effort. */
export function reportPage404(path: string) {
  return telemetryClient.post('/api/telemetry/page-404', { path })
}
