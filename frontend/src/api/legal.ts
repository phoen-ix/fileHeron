/* Anonymous legal-page content (imprint / privacy). Standalone axios instance
 * (no auth interceptor) like api/publicLinks.ts - these pages must render for
 * logged-out visitors. */
import axios from 'axios'

export interface LegalContentResponse {
  enabled: boolean
  html_en: string
  html_de: string
}

const legalClient = axios.create({ baseURL: '/' })

export function getLegal(kind: 'imprint' | 'privacy') {
  return legalClient.get<LegalContentResponse>(`/api/legal/${kind}`)
}
