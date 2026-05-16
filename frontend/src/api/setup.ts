import api from './client'

export interface SetupStatusResponse {
  required: boolean
}

export interface CompleteSetupRequest {
  email: string
  password: string
  display_name: string
}

export interface CompleteSetupResponse {
  user_id: number
  email: string
}

export function getSetupStatus() {
  return api.get<SetupStatusResponse>('/setup/status')
}

export function completeSetup(payload: CompleteSetupRequest) {
  return api.post<CompleteSetupResponse>('/setup/admin', payload)
}
