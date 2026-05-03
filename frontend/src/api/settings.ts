import api from './client'

export type OIDCPreset = 'entra' | 'google' | 'authentik' | 'keycloak' | 'custom'

export interface OIDCProviderItem {
  id: string
  name: string
  preset: OIDCPreset
  issuer_url: string
  client_id: string
  client_secret_set: boolean
  groups_claim: string
  admin_groups: string
  employee_groups: string
  redirect_uri: string
  enabled: boolean
  user_count: number
  created_at: string
  updated_at: string
}

export interface OIDCProviderListResponse {
  items: OIDCProviderItem[]
}

export interface CreateOIDCProviderRequest {
  name: string
  preset: OIDCPreset
  issuer_url: string
  client_id: string
  client_secret: string
  groups_claim?: string
  admin_groups?: string
  employee_groups?: string
  redirect_uri?: string
  enabled?: boolean
}

export interface UpdateOIDCProviderRequest {
  name?: string
  preset?: OIDCPreset
  issuer_url?: string
  client_id?: string
  /** null/undefined = leave unchanged; '' = clear; other = replace. */
  client_secret?: string | null
  groups_claim?: string
  admin_groups?: string
  employee_groups?: string
  redirect_uri?: string
  enabled?: boolean
}

export interface TestConnectionResponse {
  ok: boolean
  issuer?: string
  authorization_endpoint?: string
  token_endpoint?: string
  error?: string
}

export interface PresetField {
  key: string
  label: string
  placeholder: string
}

export interface PresetMeta {
  preset: OIDCPreset
  label: string
  issuer?: string | null
  issuer_template?: string | null
  issuer_template_fields: PresetField[]
  default_groups_claim: string
  supports_groups: boolean
  notes: string
}

export interface PresetsResponse {
  presets: PresetMeta[]
}

export function listProviders() {
  return api.get<OIDCProviderListResponse>('/admin/settings/sso/providers')
}

export function getProvider(id: string) {
  return api.get<OIDCProviderItem>(`/admin/settings/sso/providers/${id}`)
}

export function createProvider(payload: CreateOIDCProviderRequest) {
  return api.post<OIDCProviderItem>('/admin/settings/sso/providers', payload)
}

export function updateProvider(id: string, payload: UpdateOIDCProviderRequest) {
  return api.patch<OIDCProviderItem>(
    `/admin/settings/sso/providers/${id}`,
    payload,
  )
}

export function deleteProvider(id: string) {
  return api.delete(`/admin/settings/sso/providers/${id}`)
}

export function testProviderConnection(
  id: string,
  payload: { issuer_url?: string },
) {
  return api.post<TestConnectionResponse>(
    `/admin/settings/sso/providers/${id}/test-connection`,
    payload,
  )
}

export function testDiscovery(payload: { issuer_url?: string }) {
  return api.post<TestConnectionResponse>(
    '/admin/settings/sso/test-discovery',
    payload,
  )
}

export function listPresets() {
  return api.get<PresetsResponse>('/admin/settings/sso/presets')
}
