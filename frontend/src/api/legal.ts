/* Anonymous legal-page content (imprint / privacy). The bare client
 * (no auth interceptor) like api/publicLinks.ts - these pages must render for
 * logged-out visitors. */
import { createBareClient } from './client'

export interface LegalContentResponse {
  enabled: boolean
  html_en: string
  html_de: string
}

const legalClient = createBareClient('/')

export function getLegal(kind: 'imprint' | 'privacy') {
  return legalClient.get<LegalContentResponse>(`/api/legal/${kind}`)
}
