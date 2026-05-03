import api from './client'

import type { OIDCPreset } from './settings'

export interface ConnectStartResponse {
  redirect_url: string
}

export interface OIDCLinkItem {
  provider_id: string
  provider_name: string
  preset: OIDCPreset
  sub_hint: string
}

export interface OIDCLinkResponse {
  link: OIDCLinkItem | null
}

export function getOIDCLink() {
  return api.get<OIDCLinkResponse>('/account/oidc/links')
}

export function startConnect(providerId: string) {
  return api.post<ConnectStartResponse>(
    `/account/oidc/connect/start/${providerId}`,
    {},
  )
}

export function disconnectOIDC() {
  return api.delete('/account/oidc/links')
}
