/* Pinia store for instance-wide site config (the response of
 * /api/config-public). Hydrated once at app bootstrap so display-time
 * code - most importantly the `formatInSiteTime` helper in
 * utils/datetime.ts - can read the admin-set timezone synchronously
 * without re-fetching per render.
 *
 * Distinct from `auth.ts` (which is per-user) because the values here
 * are instance-wide and must be available pre-login (anonymous login
 * page renders MOTD + OIDC provider list + timestamps in any embedded
 * content).
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  getPublicConfig,
  type PublicBranding,
  type PublicConfigResponse,
  type PublicLegal,
  type PublicProvider,
} from '@/api/oidc'

const DEFAULT_TIMEZONE = 'UTC'

const EMPTY_BRANDING: PublicBranding = {
  logo_url: null,
  link_url: null,
  show_header: false,
  show_login: false,
  show_public: false,
}
const EMPTY_LEGAL: PublicLegal = { imprint_enabled: false, privacy_enabled: false }

export const useSiteStore = defineStore('site', () => {
  const appName = ref<string>('file:Heron')
  const defaultLocale = ref<'en' | 'de'>('en')
  const providers = ref<PublicProvider[]>([])
  const motd = ref<{ text: string } | null>(null)
  const runningVersion = ref<string>('')
  const timezone = ref<string>(DEFAULT_TIMEZONE)
  const branding = ref<PublicBranding>({ ...EMPTY_BRANDING })
  const legal = ref<PublicLegal>({ ...EMPTY_LEGAL })
  const maintenance = ref<{ enabled: boolean; message: string } | null>(null)
  const loaded = ref(false)

  function _apply(cfg: PublicConfigResponse) {
    appName.value = cfg.app_name
    defaultLocale.value = cfg.default_locale
    providers.value = cfg.providers
    motd.value = cfg.motd ?? null
    runningVersion.value = cfg.running_version
    timezone.value = cfg.site_timezone || DEFAULT_TIMEZONE
    branding.value = cfg.branding ?? { ...EMPTY_BRANDING }
    legal.value = cfg.legal ?? { ...EMPTY_LEGAL }
    maintenance.value = cfg.maintenance ?? null
    loaded.value = true
  }

  async function loadConfig(): Promise<void> {
    try {
      const { data } = await getPublicConfig()
      _apply(data)
    } catch {
      // Fail-open: SPA still renders with defaults if /api/config-public
      // is briefly unreachable. Real auth-required surfaces will surface
      // their own errors when the user navigates.
    }
  }

  return {
    appName,
    defaultLocale,
    providers,
    motd,
    runningVersion,
    timezone,
    branding,
    legal,
    maintenance,
    loaded,
    loadConfig,
  }
})
