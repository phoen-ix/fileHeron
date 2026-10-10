import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { i18n, setLocale } from './i18n'
import router from './router'
import { useAuthStore } from './stores/auth'
import { useSiteStore } from './stores/site'
import { useUiStore } from './stores/ui'
import './styles/global.css'
import { installStaleBundleRecovery } from './utils/staleBundle'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(i18n)
app.use(router)

// A tab opened before an update reloads instead of dead-ending on a chunk the
// update replaced (utils/staleBundle.ts). The toast is the fallback for when it
// already reloaded once and the chunk is still missing.
installStaleBundleRecovery(router, () =>
  useUiStore().pushToast(i18n.global.t('common.new_version'), 'warn', 0),
)

// Wire up auth bootstrap + auth-lost handler before mounting so the router's
// first beforeEach can wait for the silent-refresh result.
const auth = useAuthStore()
auth.registerAuthLostHandler(() => {
  // A public route (e.g. /d/:token) must never be bounced to login by a
  // background 401 - the page works fully logged-out.
  if (router.currentRoute.value.meta.public === true) return
  const path = router.currentRoute.value.fullPath
  router.push({
    name: 'login',
    query: path && path !== '/login' && path !== '/' ? { redirect: path } : undefined,
  })
})

// Hydrate site config (timezone, MOTD, OIDC providers) in parallel with
// the silent-refresh attempt. Both must resolve before mount so the
// router's first beforeEach sees auth state AND the formatInSiteTime
// helper has a real timezone (not the UTC default placeholder) for the
// first paint of any view that renders timestamps.
const site = useSiteStore()
Promise.all([auth.bootstrap(), site.loadConfig()]).finally(() => {
  if (auth.user) setLocale(auth.user.locale)
  app.mount('#app')
})
