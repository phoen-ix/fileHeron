<script setup lang="ts">
import { computed, ref } from 'vue'
import { onClickOutside } from '@vueuse/core'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import BrandLogo from '@/components/BrandLogo.vue'
import BrandMark from '@/components/BrandMark.vue'
import NotificationBell from '@/components/NotificationBell.vue'
import { useAuthStore } from '@/stores/auth'
import { useSiteStore } from '@/stores/site'
import { useUiStore } from '@/stores/ui'

const auth = useAuthStore()
const site = useSiteStore()
const ui = useUiStore()
const router = useRouter()
const { t } = useI18n()

const showLogo = computed(
  () => site.branding.show_header && !!site.branding.logo_url,
)

const menuOpen = ref(false)
const menuRoot = ref<HTMLElement | null>(null)
onClickOutside(menuRoot, () => (menuOpen.value = false))

const initials = computed(() => {
  const dn = auth.user?.display_name ?? ''
  return (
    dn
      .split(/\s+/)
      .map((p) => p[0])
      .filter(Boolean)
      .slice(0, 2)
      .join('')
      .toUpperCase() || '·'
  )
})

async function doLogout() {
  // logout() clears local state either way, but returns false when the server
  // never confirmed the revoke - in which case the refresh cookie is still live
  // and the session can be restored by a reload. Say so rather than implying a
  // clean sign-out (audit 2026-07-30).
  const revoked = await auth.logout()
  if (!revoked) ui.pushToast(t('auth.logout_not_confirmed'), 'warn')
  router.push({ name: 'login' })
}
</script>

<template>
  <header class="app-header">
    <div class="app-header-inner">
      <div class="app-header-left">
        <BrandLogo
          v-if="showLogo"
          :src="site.branding.logo_url as string"
          :alt="site.appName"
          :link-url="site.branding.link_url"
          size="sm"
        />
        <BrandMark
          size="sm"
          :linkable="auth.user?.home_page_enabled !== false"
        />
        <nav class="app-nav" :aria-label="$t('header.menu')">
          <RouterLink :to="{ name: 'outbox' }" class="nav-link">
            {{ $t('header.outbox') }}
          </RouterLink>
          <RouterLink :to="{ name: 'inbox' }" class="nav-link">
            {{ $t('header.inbox') }}
          </RouterLink>
          <RouterLink
            v-if="auth.user?.can_approve_shares"
            :to="{ name: 'approvals' }"
            class="nav-link"
          >
            {{ $t('header.approvals') }}
          </RouterLink>
          <RouterLink :to="{ name: 'share-create' }" class="nav-link nav-cta">
            {{ $t('header.new_share') }}
          </RouterLink>
        </nav>
      </div>

      <div class="app-header-right">
        <NotificationBell v-if="auth.isAuthenticated" />
        <div ref="menuRoot" class="user-menu">
          <button
            type="button"
            class="user-trigger"
            :aria-expanded="menuOpen"
            :aria-label="$t('header.menu')"
            @click="menuOpen = !menuOpen"
          >
            <span class="initials">{{ initials }}</span>
            <span class="dn">{{ auth.user?.display_name }}</span>
            <span class="chev" aria-hidden="true">⌄</span>
          </button>
          <div v-if="menuOpen" class="user-pop" role="menu">
            <RouterLink
              v-if="auth.user?.role === 'admin'"
              :to="{ name: 'admin-users' }"
              class="user-pop-item"
              role="menuitem"
              @click="menuOpen = false"
            >
              {{ $t('header.admin') }}
            </RouterLink>
            <RouterLink
              to="/account"
              class="user-pop-item"
              role="menuitem"
              @click="menuOpen = false"
            >
              {{ $t('header.account') }}
            </RouterLink>
            <button type="button" class="user-pop-item user-pop-danger" role="menuitem" @click="doLogout">
              {{ $t('header.logout') }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  border-bottom: 1px solid var(--fh-hairline);
  background: rgba(250, 248, 243, 0.92);
  backdrop-filter: saturate(120%) blur(8px);
  -webkit-backdrop-filter: saturate(120%) blur(8px);
  position: sticky;
  top: 0;
  z-index: 10;
}

.app-header-inner {
  max-width: var(--fh-max-width-page);
  margin: 0 auto;
  padding: var(--fh-space-3) var(--fh-page-gutter);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fh-space-4);
}

.app-header-left {
  display: flex;
  align-items: center;
  gap: var(--fh-space-5);
}

.app-nav {
  display: flex;
  gap: var(--fh-space-4);
}

.nav-link {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fh-subtle);
  text-decoration: none;
  padding: 4px 0;
  border-bottom: 2px solid transparent;
  transition:
    color var(--fh-duration-fast) var(--fh-easing),
    border-color var(--fh-duration-fast) var(--fh-easing);
}

.nav-link:hover,
.nav-link.router-link-active {
  color: var(--fh-ink);
  border-bottom-color: var(--fh-accent);
}

.nav-cta {
  color: var(--fh-accent);
}

.app-header-right {
  display: flex;
  align-items: center;
  gap: var(--fh-space-4);
}

@media (max-width: 720px) {
  .app-nav {
    display: none;
  }
}

.user-menu {
  position: relative;
}

.user-trigger {
  display: inline-flex;
  align-items: center;
  gap: var(--fh-space-2);
  background: transparent;
  border: 1px solid transparent;
  padding: var(--fh-space-1) var(--fh-space-2);
  border-radius: var(--fh-radius-sm);
  cursor: pointer;
  font: inherit;
  color: var(--fh-ink);
  transition: border-color var(--fh-duration-fast) var(--fh-easing);
}

.user-trigger:hover {
  border-color: var(--fh-hairline-strong);
}

.initials {
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--fh-ink);
  color: var(--fh-paper);
  font-family: var(--fh-font-mono);
  font-size: 11px;
  letter-spacing: 0.04em;
}

.dn {
  font-size: var(--fh-text-body-sm);
}

.chev {
  font-size: 14px;
  color: var(--fh-subtle);
  margin-top: -2px;
}

.user-pop {
  position: absolute;
  right: 0;
  top: calc(100% + var(--fh-space-2));
  background: var(--fh-paper-raised);
  border: 1px solid var(--fh-hairline-strong);
  min-width: 200px;
  box-shadow: 0 4px 32px rgba(26, 29, 36, 0.06);
  display: flex;
  flex-direction: column;
}

.user-pop-item {
  text-align: left;
  background: none;
  border: none;
  font: inherit;
  color: var(--fh-ink);
  text-decoration: none;
  padding: var(--fh-space-2) var(--fh-space-3);
  cursor: pointer;
  display: block;
}

.user-pop-item:hover {
  background: var(--fh-paper-sunk);
  color: var(--fh-accent);
}

.user-pop-danger:hover {
  color: var(--fh-danger);
}
</style>
