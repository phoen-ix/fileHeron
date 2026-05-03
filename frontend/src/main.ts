import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { i18n, setLocale } from './i18n'
import router from './router'
import { useAuthStore } from './stores/auth'
import './styles/global.css'
import 'element-plus/dist/index.css'
import './styles/element-plus.css'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(i18n)
app.use(router)

// Wire up auth bootstrap + auth-lost handler before mounting so the router's
// first beforeEach can wait for the silent-refresh result.
const auth = useAuthStore()
auth.registerAuthLostHandler(() => {
  const path = router.currentRoute.value.fullPath
  router.push({
    name: 'login',
    query: path && path !== '/login' && path !== '/' ? { redirect: path } : undefined,
  })
})

// Sync locale from user when available; otherwise stays on detected/default.
auth.bootstrap().finally(() => {
  if (auth.user) setLocale(auth.user.locale)
  app.mount('#app')
})
