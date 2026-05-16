import { createRouter, createWebHistory, type RouteLocationNormalized } from 'vue-router'

import { effectiveLandingPath } from '@/composables/useEffectiveLanding'
import { useAuthStore } from '@/stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    /** True for routes that should NOT bounce a logged-out user to /login. */
    public?: boolean
    /** Density mode for the layout wrapper. */
    density?: 'editorial' | 'operator'
    /** Page title shown in <title> + AppHeader. */
    title?: string
    /** Restrict to a specific role; un-met → bounced home. */
    requiresRole?: 'admin'
  }
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    /* public --------------------------------------------------------------- */
    {
      path: '/setup',
      name: 'setup',
      component: () => import('@/views/Setup.vue'),
      meta: { public: true, density: 'editorial', title: 'Set up fileHeron' },
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/Login.vue'),
      meta: { public: true, density: 'editorial', title: 'Sign in' },
    },
    {
      path: '/register/:token',
      name: 'register',
      component: () => import('@/views/RegisterFromInvite.vue'),
      meta: { public: true, density: 'editorial', title: 'Set up your account' },
    },
    {
      path: '/forgot-password',
      name: 'forgot-password',
      component: () => import('@/views/ForgotPassword.vue'),
      meta: { public: true, density: 'editorial', title: 'Forgot password' },
    },
    {
      path: '/reset-password/:token',
      name: 'reset-password',
      component: () => import('@/views/ResetPassword.vue'),
      meta: { public: true, density: 'editorial', title: 'Reset password' },
    },
    {
      path: '/verify-email/:token',
      name: 'verify-email',
      component: () => import('@/views/EmailVerify.vue'),
      meta: { public: true, density: 'editorial', title: 'Verify email' },
    },

    /* authed --------------------------------------------------------------- */
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomePlaceholder.vue'),
      meta: { density: 'editorial', title: 'file:Heron' },
    },
    {
      path: '/account',
      name: 'account',
      component: () => import('@/views/Account.vue'),
      meta: { density: 'editorial', title: 'Account' },
    },
    {
      path: '/account/2fa',
      name: 'account-2fa',
      component: () => import('@/views/TwoFactorSetup.vue'),
      meta: { density: 'editorial', title: 'Two-factor authentication' },
    },

    /* shares -------------------------------------------------------------- */
    {
      path: '/outbox',
      name: 'outbox',
      component: () => import('@/views/ShareList.vue'),
      meta: { density: 'operator', title: 'Sent' },
    },
    {
      path: '/inbox',
      name: 'inbox',
      component: () => import('@/views/ShareList.vue'),
      meta: { density: 'operator', title: 'Received' },
    },
    {
      path: '/share/new',
      name: 'share-create',
      component: () => import('@/views/ShareCreate.vue'),
      meta: { density: 'operator', title: 'New share' },
    },
    {
      path: '/share/:id',
      name: 'share-detail',
      component: () => import('@/views/ShareDetail.vue'),
      meta: { density: 'operator', title: 'Share' },
    },

    /* admin --------------------------------------------------------------- */
    {
      path: '/admin',
      component: () => import('@/views/AdminLayout.vue'),
      meta: { density: 'operator', requiresRole: 'admin' },
      children: [
        {
          path: '',
          redirect: { name: 'admin-users' },
        },
        {
          path: 'users',
          name: 'admin-users',
          component: () => import('@/views/AdminUsers.vue'),
          meta: { density: 'operator', title: 'Users', requiresRole: 'admin' },
        },
        {
          path: 'users/:id',
          name: 'admin-user-detail',
          component: () => import('@/views/AdminUserDetail.vue'),
          meta: { density: 'operator', title: 'User', requiresRole: 'admin' },
        },
        {
          path: 'groups',
          name: 'admin-groups',
          component: () => import('@/views/AdminGroups.vue'),
          meta: { density: 'operator', title: 'Groups', requiresRole: 'admin' },
        },
        {
          path: 'groups/:id',
          name: 'admin-group-detail',
          component: () => import('@/views/AdminGroupDetail.vue'),
          meta: { density: 'operator', title: 'Group', requiresRole: 'admin' },
        },
        {
          path: 'audit-log',
          name: 'admin-audit',
          component: () => import('@/views/AdminAuditLog.vue'),
          meta: { density: 'operator', title: 'Audit log', requiresRole: 'admin' },
        },
        {
          path: 'file-history',
          name: 'admin-file-history',
          component: () => import('@/views/AdminFileHistory.vue'),
          meta: { density: 'operator', title: 'File history', requiresRole: 'admin' },
        },
        {
          path: 'quarantine',
          name: 'admin-quarantine',
          component: () => import('@/views/AdminQuarantine.vue'),
          meta: { density: 'operator', title: 'Quarantine', requiresRole: 'admin' },
        },
        {
          path: 'api-tokens',
          name: 'admin-api-tokens',
          component: () => import('@/views/AdminApiTokens.vue'),
          meta: { density: 'operator', title: 'API tokens', requiresRole: 'admin' },
        },
        {
          path: 'settings',
          name: 'admin-settings',
          component: () => import('@/views/AdminSettings.vue'),
          meta: { density: 'operator', title: 'Settings', requiresRole: 'admin' },
        },
        {
          path: 'settings/sso',
          name: 'admin-settings-sso',
          component: () => import('@/views/AdminSettingsSSOList.vue'),
          meta: { density: 'operator', title: 'SSO', requiresRole: 'admin' },
        },
        {
          path: 'settings/sso/new',
          name: 'admin-settings-sso-new',
          component: () => import('@/views/AdminSettingsSSOEdit.vue'),
          meta: { density: 'operator', title: 'New SSO provider', requiresRole: 'admin' },
        },
        {
          path: 'settings/sso/:id',
          name: 'admin-settings-sso-edit',
          component: () => import('@/views/AdminSettingsSSOEdit.vue'),
          meta: { density: 'operator', title: 'Edit SSO provider', requiresRole: 'admin' },
        },
        {
          path: 'settings/api-tokens',
          name: 'admin-settings-api-tokens',
          component: () => import('@/views/AdminSettingsApiTokens.vue'),
          meta: { density: 'operator', title: 'API token policy', requiresRole: 'admin' },
        },
        {
          path: 'settings/public-links',
          name: 'admin-settings-public-links',
          component: () => import('@/views/AdminSettingsPublicLinks.vue'),
          meta: { density: 'operator', title: 'Public link policy', requiresRole: 'admin' },
        },
        {
          path: 'settings/email',
          name: 'admin-settings-email',
          component: () => import('@/views/AdminSettingsEmail.vue'),
          meta: { density: 'operator', title: 'Email / SMTP', requiresRole: 'admin' },
        },
        {
          path: 'settings/general',
          name: 'admin-settings-general',
          component: () => import('@/views/AdminSettingsGeneral.vue'),
          meta: { density: 'operator', title: 'General settings', requiresRole: 'admin' },
        },
        {
          // Legacy bookmark: the dedicated home-page view was folded
          // into the General settings page as a section. Redirect
          // anchors to that section.
          path: 'settings/home-page',
          redirect: { name: 'admin-settings-general', hash: '#home-page' },
        },
        {
          path: 'settings/twofa',
          name: 'admin-settings-twofa',
          component: () => import('@/views/AdminSettingsTwofa.vue'),
          meta: { density: 'operator', title: '2FA enforcement', requiresRole: 'admin' },
        },
        {
          path: 'settings/quarantine',
          name: 'admin-settings-quarantine',
          component: () => import('@/views/AdminSettingsQuarantine.vue'),
          meta: { density: 'operator', title: 'Quarantine', requiresRole: 'admin' },
        },
        {
          path: 'system',
          name: 'admin-system',
          component: () => import('@/views/AdminSystem.vue'),
          meta: { density: 'operator', title: 'System', requiresRole: 'admin' },
        },
      ],
    },
    {
      // Legacy notice page — superseded by auto-launch on /account/2fa
      // when requires_2fa is true. Kept as a redirect so any existing
      // bookmarks or external links still resolve. The interstitial
      // notice + extra "Set up 2FA" click made the enrolment flow
      // three clicks deep; users now land on the QR directly.
      path: '/account/2fa/forced',
      redirect: { name: 'account-2fa' },
    },

    /* public share landing page ------------------------------------------- */
    {
      path: '/d/:token',
      name: 'public-share',
      component: () => import('@/views/PublicShare.vue'),
      meta: { public: true, density: 'editorial', title: 'Shared files' },
    },

    /* fallback ------------------------------------------------------------- */
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFound.vue'),
      meta: { public: true, density: 'editorial', title: 'Not found' },
    },
  ],
})

function isPublic(route: RouteLocationNormalized): boolean {
  return route.meta.public === true
}

router.beforeEach(async (to, _from) => {
  const auth = useAuthStore()
  // Wait for the silent-refresh bootstrap. main.ts already kicked it off
  // before mount; auth.bootstrap() returns the cached in-flight promise so
  // every guard call awaits the same resolution. After it settles, this
  // line is a no-op (resolved promise).
  await auth.bootstrap()

  // v1.0.0 first-install: if no admin exists, every route bounces to
  // /setup; the setup route 404s once an admin exists.
  if (auth.setupRequired && to.name !== 'setup') {
    return { name: 'setup' }
  }
  if (!auth.setupRequired && to.name === 'setup') {
    return { name: 'login' }
  }

  if (!isPublic(to) && !auth.isAuthenticated) {
    return {
      name: 'login',
      query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined,
    }
  }

  // Logged-in users hitting /login → bounce to their effective landing
  // (avoids a double form on F5). Falls back gracefully when home is
  // disabled.
  if (to.name === 'login' && auth.isAuthenticated) {
    return { path: effectiveLandingPath(auth.user) }
  }

  // Admin-only routes — bounce non-admins to their effective landing.
  if (
    to.meta.requiresRole === 'admin' &&
    auth.isAuthenticated &&
    auth.user?.role !== 'admin'
  ) {
    return { path: effectiveLandingPath(auth.user) }
  }

  // Hitting `/` while admin has disabled the home page → redirect
  // forward. Bookmarks + direct URL entry still work; they just hop.
  if (
    to.name === 'home' &&
    auth.isAuthenticated &&
    auth.user?.home_page_enabled === false
  ) {
    return { path: effectiveLandingPath(auth.user) }
  }

  // Forced-2FA: when the active policy applies to this user and they
  // haven't enabled TOTP yet, drop them straight into the setup
  // wizard (which auto-launches the QR + secret display). Backend
  // would 403 anyway; this just routes to the QR first. The original
  // destination rides along as `?redirect=` so completion can return
  // there.
  if (
    auth.isAuthenticated &&
    auth.user?.requires_2fa === true &&
    to.name !== 'login' &&
    !to.path.startsWith('/account/2fa')
  ) {
    return {
      name: 'account-2fa',
      query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined,
    }
  }
})

router.afterEach((to) => {
  const t = to.meta.title ?? 'file:Heron'
  document.title = t === 'file:Heron' ? 'file:Heron' : `${t} · file:Heron`
})

export default router
