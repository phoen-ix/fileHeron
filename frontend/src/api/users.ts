import api from './client'
import type { UserSearchResponse } from '@/types/api'

export function searchUsers(q: string) {
  return api.get<UserSearchResponse>('/users/search', { params: { q } })
}
