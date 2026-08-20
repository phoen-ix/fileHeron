import api from './client'

import type { OIDCPreset } from './settings'

export interface PublicProvider {
  id: string
  name: string
  preset: OIDCPreset
}

export interface PublicBranding {
  /** URL of the uploaded logo, or null when none is set. */
  logo_url: string | null
  /** Optional URL the logo links to. */
  link_url: string | null
  show_header: boolean
  show_login: boolean
  show_public: boolean
}

export interface PublicLegal {
  imprint_enabled: boolean
  privacy_enabled: boolean
}

export interface PublicConfigResponse {
  app_name: string
  default_locale: 'en' | 'de'
  /** One entry per enabled, usable OIDC provider. */
  providers: PublicProvider[]
  /** Admin-set login-page banner. Absent when disabled or text is empty. */
  motd?: { text: string }
  /** Running app version (baked at image build). Phase 1 self-update. */
  /** Admin-set site-wide display timezone (IANA name). Always present;
   *  defaults to "UTC" when unset. Drives every timestamp formatter via
   *  the site Pinia store. */
  site_timezone: string
  max_direct_upload_bytes: number
  /** Site branding (logo + display surfaces + link). */
  branding: PublicBranding
  /** Which legal footer pages are enabled. */
  legal: PublicLegal
  /** Maintenance mode: present + enabled when new transfers are paused. */
  maintenance?: { enabled: boolean; message: string }
}

export function getPublicConfig() {
  return api.get<PublicConfigResponse>('/config-public')
}

/** Browser-driven: navigate to /api/auth/oidc/start/{providerId}. The
 * backend 302-redirects to the IdP. */
export function oidcStartUrl(providerId: string): string {
  return `/api/auth/oidc/start/${encodeURIComponent(providerId)}`
}
