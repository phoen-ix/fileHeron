import api from './client'
import type { LoginResponse } from '@/types/api'

export function registerFromInvite(payload: {
  token: string
  password: string
  display_name: string
  locale: 'en' | 'de'
}) {
  return api.post<LoginResponse>('/auth/register-from-invite', payload)
}

export function login(payload: { email: string; password: string; totp_code?: string }) {
  return api.post<LoginResponse>('/auth/login', payload)
}

export function loginWithRecovery(payload: {
  email: string
  password: string
  recovery_code: string
}) {
  return api.post<LoginResponse>('/auth/login/recovery', payload)
}

export function refresh() {
  return api.post('/auth/refresh')
}

export function logout() {
  return api.post('/auth/logout')
}

export function forgotPassword(payload: { email: string }) {
  return api.post('/auth/forgot-password', payload)
}

export function resetPassword(payload: { token: string; new_password: string }) {
  return api.post('/auth/reset-password', payload)
}

export function verifyEmail(payload: { token: string }) {
  return api.post('/auth/verify-email', payload)
}

export function confirmEmailChange(payload: { token: string }) {
  return api.post<{
    ok: boolean
    applied: boolean
    pending_side: string | null
    oidc_reset: boolean
    set_password_required: boolean
  }>('/auth/confirm-email-change', payload)
}

export function cancelEmailChange(payload: { token: string }) {
  return api.post<{ ok: boolean }>('/auth/cancel-email-change', payload)
}
