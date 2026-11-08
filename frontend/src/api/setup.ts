import api from './client'

export interface SetupStatusResponse {
  required: boolean
  token_required: boolean
}

export interface CompleteSetupRequest {
  email: string
  password: string
  display_name: string
  setup_token?: string | null
  /** The language the wizard was shown in; the first admin's account locale. */
  locale?: 'en' | 'de'
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
