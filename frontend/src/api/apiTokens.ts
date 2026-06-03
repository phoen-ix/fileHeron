import api from './client'
import type {
  ApiTokenListResponse,
  CreateApiTokenResponse,
} from '@/types/api'

export function listTokens() {
  return api.get<ApiTokenListResponse>('/account/api-tokens')
}

export function createToken(name: string, expiresAt: string | null = null) {
  return api.post<CreateApiTokenResponse>('/account/api-tokens', {
    name,
    expires_at: expiresAt,
  })
}

export function revokeToken(tokenId: number) {
  return api.delete(`/account/api-tokens/${tokenId}`)
}
