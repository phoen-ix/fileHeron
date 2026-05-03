import api from './client'
import type {
  RecoveryCodesResponse,
  TotpSetupResponse,
  TotpStatusResponse,
} from '@/types/api'

export function getStatus() {
  return api.get<TotpStatusResponse>('/account/2fa/status')
}

export function beginSetup() {
  return api.post<TotpSetupResponse>('/account/2fa/setup')
}

export function enable(payload: { code: string }) {
  return api.post<RecoveryCodesResponse>('/account/2fa/enable', payload)
}

export function disable(payload: { password: string; code_or_recovery: string }) {
  return api.post('/account/2fa/disable', payload)
}

export function regenerateRecoveryCodes(payload: {
  password: string
  code_or_recovery: string
}) {
  return api.post<RecoveryCodesResponse>('/account/2fa/recovery-codes/regenerate', payload)
}
