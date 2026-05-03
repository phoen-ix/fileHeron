import api from './client'

import type { OIDCPreset } from './settings'

export interface PublicProvider {
  id: string
  name: string
  preset: OIDCPreset
}

export interface PublicConfigResponse {
  app_name: string
  default_locale: 'en' | 'de'
  /** Phase 10: one entry per enabled, usable OIDC provider. */
  providers: PublicProvider[]
}

export function getPublicConfig() {
  return api.get<PublicConfigResponse>('/config-public')
}

/** Browser-driven: navigate to /api/auth/oidc/start/{providerId}. The
 * backend 302-redirects to the IdP. */
export function oidcStartUrl(providerId: string): string {
  return `/api/auth/oidc/start/${encodeURIComponent(providerId)}`
}
