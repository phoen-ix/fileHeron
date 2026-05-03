import api from './client'
import type {
  ApiTokenListResponse,
  CreateApiTokenResponse,
} from '@/types/api'

export function listTokens() {
  return api.get<ApiTokenListResponse>('/account/api-tokens')
}

export function createToken(name: string) {
  return api.post<CreateApiTokenResponse>('/account/api-tokens', { name })
}

export function revokeToken(tokenId: number) {
  return api.delete(`/account/api-tokens/${tokenId}`)
}
