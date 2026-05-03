import api from './client'
import type {
  CreateGroupRequest,
  GroupDetailResponse,
  GroupListResponse,
  GroupResponse,
  UpdateGroupRequest,
} from '@/types/api'

export function listGroups() {
  return api.get<GroupListResponse>('/groups')
}

export function getGroup(id: number) {
  return api.get<GroupDetailResponse>(`/groups/${id}`)
}

export function createGroup(payload: CreateGroupRequest) {
  return api.post<GroupResponse>('/groups', payload)
}

export function updateGroup(id: number, payload: UpdateGroupRequest) {
  return api.patch<GroupResponse>(`/groups/${id}`, payload)
}

export function deleteGroup(id: number) {
  return api.delete(`/groups/${id}`)
}

export function addMembers(id: number, userIds: number[]) {
  return api.post<GroupDetailResponse>(`/groups/${id}/members`, { user_ids: userIds })
}

export function removeMember(groupId: number, userId: number) {
  return api.delete(`/groups/${groupId}/members/${userId}`)
}

export function listRecipientTargetGroups() {
  return api.get<GroupListResponse>('/groups/recipient-targets')
}
