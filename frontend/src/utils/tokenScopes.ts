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
] as const

type TokenScope = (typeof TOKEN_SCOPES)[number]

/** Display grouping for the create form (purely presentational). */
export const TOKEN_SCOPE_GROUPS: { group: 'sharing' | 'files'; scopes: TokenScope[] }[] = [
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
]

/** i18n key for a scope label, e.g. "files:upload" -> "api_tokens.scopes.files_upload". */
export function scopeLabelKey(scope: string): string {
  return `api_tokens.scopes.${scope.replace(/:/g, '_')}`
}
