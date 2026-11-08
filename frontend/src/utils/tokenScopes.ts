/**
 * Canonical API-token scopes - mirrors backend services/api_token.py::SCOPES.
 * A token's `scopes` is null (unrestricted / full access) or a subset of these.
 * Keep this list and the backend SCOPES frozenset in lockstep.
 */
export const TOKEN_SCOPES = [
  'shares:create',
  'shares:add_files',
  'shares:read',
  'shares:manage',
  'recipients:search',
  'public_links:read',
  'public_links:write',
  'files:upload',
  'files:download',
  'files:delete',
  'secrets:send',
  'secrets:read',
  'secrets:reveal',
  'secrets:manage',
  'secrets:links',
  'secrets:request',
] as const

type TokenScope = (typeof TOKEN_SCOPES)[number]

/** Display grouping for the create form (purely presentational). */
export const TOKEN_SCOPE_GROUPS: {
  group: 'sharing' | 'files' | 'secrets'
  scopes: TokenScope[]
}[] = [
  {
    group: 'sharing',
    scopes: [
      'shares:create',
      'shares:add_files',
      'shares:read',
      'shares:manage',
      'recipients:search',
      'public_links:read',
      'public_links:write',
    ],
  },
  { group: 'files', scopes: ['files:upload', 'files:download', 'files:delete'] },
  {
    group: 'secrets',
    scopes: [
      'secrets:send',
      'secrets:read',
      'secrets:reveal',
      'secrets:manage',
      'secrets:links',
      'secrets:request',
    ],
  },
]

/** i18n key for a scope label, e.g. "files:upload" -> "api_tokens.scopes.files_upload". */
export function scopeLabelKey(scope: string): string {
  return `api_tokens.scopes.${scope.replace(/:/g, '_')}`
}

/** Token-appropriate expiry presets. The forms open on
 *  `defaultTokenExpiryLocal()` (90 days); "never" is offered but has to be
 *  picked. One copy for the account and the admin token forms, which each had
 *  their own (audit 2026-11-08). */
export const TOKEN_EXPIRY_PRESETS = ['7d', '30d', '90d', '1y', 'never'] as const
