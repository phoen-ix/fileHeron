import {
  createRouter,
  createWebHistory,
  type RouteLocationNormalized,
  type RouteLocationRaw,
} from 'vue-router'

import { effectiveLandingPath } from '@/composables/useEffectiveLanding'
import { i18n } from '@/i18n'
import { takePostLoginRedirect } from '@/router/postLoginRedirect'
import { useAuthStore } from '@/stores/auth'
import type { MeResponse } from '@/types/api'

declare module 'vue-router' {
  interface RouteMeta {
    /** True for routes that should NOT bounce a logged-out user to /login. */
    public?: boolean
    /** Density mode for the layout wrapper. */
    density?: 'editorial' | 'operator'
    /** i18n key under `page_title.*` for <title>. A key, not a string: every
     *  title used to be hardcoded English, so a German user's tab, history
     *  entry and bookmark were always English (audit 2026-07-30). */
    titleKey?: string
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
      meta: { public: true, density: 'editorial', titleKey: 'setup' },
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/Login.vue'),
      meta: { public: true, density: 'editorial', titleKey: 'login' },
    },
    {
      // Second factor for a login whose first factor was SSO or a passkey.
      // Public because the browser holds no session at this point - it carries
      // only a short-lived pending token that grants nothing on its own.
      path: '/login/2fa',
      name: 'login-2fa',
      component: () => import('@/views/LoginSecondFactor.vue'),
      meta: { public: true, density: 'editorial', titleKey: 'login' },
    },
    {
      path: '/register/:token',
      name: 'register',
      component: () => import('@/views/RegisterFromInvite.vue'),
      meta: { public: true, density: 'editorial', titleKey: 'register' },
    },
    {
      path: '/forgot-password',
      name: 'forgot-password',
      component: () => import('@/views/ForgotPassword.vue'),
      meta: { public: true, density: 'editorial', titleKey: 'forgot_password' },
    },
    {
      path: '/reset-password/:token',
      name: 'reset-password',
      component: () => import('@/views/ResetPassword.vue'),
      meta: { public: true, density: 'editorial', titleKey: 'reset_password' },
    },
    {
      path: '/verify-email/:token',
      name: 'verify-email',
      component: () => import('@/views/EmailVerify.vue'),
      meta: { public: true, density: 'editorial', titleKey: 'verify_email' },
    },
    {
      path: '/confirm-email-change/:token',
      name: 'confirm-email-change',
      component: () => import('@/views/ConfirmEmailChange.vue'),
      meta: { public: true, density: 'editorial', titleKey: 'confirm_email_change' },
    },
    {
      path: '/cancel-email-change/:token',
      name: 'cancel-email-change',
      component: () => import('@/views/CancelEmailChange.vue'),
      meta: { public: true, density: 'editorial', titleKey: 'cancel_email_change' },
    },

    /* authed --------------------------------------------------------------- */
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomePlaceholder.vue'),
      meta: { density: 'editorial', titleKey: 'home' },
    },
    {
      path: '/account',
      name: 'account',
      component: () => import('@/views/Account.vue'),
      meta: { density: 'editorial', titleKey: 'account' },
    },
    {
      path: '/account/2fa',
      name: 'account-2fa',
      component: () => import('@/views/TwoFactorSetup.vue'),
      meta: { density: 'editorial', titleKey: 'account_2fa' },
    },

    /* shares -------------------------------------------------------------- */
    {
      path: '/outbox',
      name: 'outbox',
      component: () => import('@/views/ShareList.vue'),
      meta: { density: 'operator', titleKey: 'outbox' },
    },
    {
      path: '/inbox',
      name: 'inbox',
      component: () => import('@/views/ShareList.vue'),
      meta: { density: 'operator', titleKey: 'inbox' },
    },
    {
      path: '/share/new',
      name: 'share-create',
      component: () => import('@/views/ShareCreate.vue'),
      meta: { density: 'operator', titleKey: 'share_create' },
    },
    {
      path: '/approvals',
      name: 'approvals',
      component: () => import('@/views/Approvals.vue'),
      meta: { density: 'operator', titleKey: 'approvals' },
    },
    {
      path: '/share/:id',
      name: 'share-detail',
      component: () => import('@/views/ShareDetail.vue'),
      meta: { density: 'operator', titleKey: 'share_detail' },
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
          meta: { density: 'operator', titleKey: 'admin_users', requiresRole: 'admin' },
        },
        {
          path: 'users/:id',
          name: 'admin-user-detail',
          component: () => import('@/views/AdminUserDetail.vue'),
          meta: { density: 'operator', titleKey: 'admin_user_detail', requiresRole: 'admin' },
        },
        {
          path: 'groups',
          name: 'admin-groups',
          component: () => import('@/views/AdminGroups.vue'),
          meta: { density: 'operator', titleKey: 'admin_groups', requiresRole: 'admin' },
        },
        {
          path: 'groups/:id',
          name: 'admin-group-detail',
          component: () => import('@/views/AdminGroupDetail.vue'),
          meta: { density: 'operator', titleKey: 'admin_group_detail', requiresRole: 'admin' },
        },
        {
          path: 'audit-log',
          name: 'admin-audit',
          component: () => import('@/views/AdminAuditLog.vue'),
          meta: { density: 'operator', titleKey: 'admin_audit', requiresRole: 'admin' },
        },
        {
          path: 'analytics',
          name: 'admin-analytics',
          component: () => import('@/views/AdminAnalytics.vue'),
          meta: { density: 'operator', titleKey: 'admin_analytics', requiresRole: 'admin' },
        },
        {
          path: 'settings/webhooks',
          name: 'admin-settings-webhooks',
          component: () => import('@/views/AdminSettingsWebhooks.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_webhooks', requiresRole: 'admin' },
        },
        {
          path: 'mail-log',
          name: 'admin-mail-log',
          component: () => import('@/views/AdminMailLog.vue'),
          meta: { density: 'operator', titleKey: 'admin_mail_log', requiresRole: 'admin' },
        },
        {
          path: 'mail-log/:id',
          name: 'admin-mail-detail',
          component: () => import('@/views/AdminMailDetail.vue'),
          meta: { density: 'operator', titleKey: 'admin_mail_detail', requiresRole: 'admin' },
        },
        {
          path: 'error-log',
          name: 'admin-error-log',
          component: () => import('@/views/AdminErrorLog.vue'),
          meta: { density: 'operator', titleKey: 'admin_error_log', requiresRole: 'admin' },
        },
        {
          path: 'inbox',
          name: 'admin-inbox',
          component: () => import('@/views/AdminInbox.vue'),
          meta: { density: 'operator', titleKey: 'admin_inbox', requiresRole: 'admin' },
        },
        {
          path: 'inbox/:id',
          name: 'admin-inbox-detail',
          component: () => import('@/views/AdminInboxDetail.vue'),
          meta: { density: 'operator', titleKey: 'admin_inbox_detail', requiresRole: 'admin' },
        },
        {
          path: 'file-history',
          name: 'admin-file-history',
          component: () => import('@/views/AdminFileHistory.vue'),
          meta: { density: 'operator', titleKey: 'admin_file_history', requiresRole: 'admin' },
        },
        {
          path: 'sessions',
          name: 'admin-sessions',
          component: () => import('@/views/AdminSessions.vue'),
          meta: { density: 'operator', titleKey: 'admin_sessions', requiresRole: 'admin' },
        },
        {
          path: 'quarantine',
          name: 'admin-quarantine',
          component: () => import('@/views/AdminQuarantine.vue'),
          meta: { density: 'operator', titleKey: 'admin_quarantine', requiresRole: 'admin' },
        },
        {
          path: 'api-tokens',
          name: 'admin-api-tokens',
          component: () => import('@/views/AdminApiTokens.vue'),
          meta: { density: 'operator', titleKey: 'admin_api_tokens', requiresRole: 'admin' },
        },
        {
          // The dedicated Settings hub page was flattened into the sidebar -
          // every sub-page is now a first-class nav item. Keep the name so any
          // `{ name: 'admin-settings' }` link still resolves.
          path: 'settings',
          name: 'admin-settings',
          redirect: { name: 'admin-settings-general' },
        },
        {
          path: 'settings/sso',
          name: 'admin-settings-sso',
          component: () => import('@/views/AdminSettingsSSOList.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_sso', requiresRole: 'admin' },
        },
        {
          path: 'settings/sso/new',
          name: 'admin-settings-sso-new',
          component: () => import('@/views/AdminSettingsSSOEdit.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_sso_new', requiresRole: 'admin' },
        },
        {
          path: 'settings/sso/:id',
          name: 'admin-settings-sso-edit',
          component: () => import('@/views/AdminSettingsSSOEdit.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_sso_edit', requiresRole: 'admin' },
        },
        {
          path: 'settings/api-tokens',
          name: 'admin-settings-api-tokens',
          component: () => import('@/views/AdminSettingsApiTokens.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_api_tokens', requiresRole: 'admin' },
        },
        {
          path: 'settings/public-links',
          name: 'admin-settings-public-links',
          component: () => import('@/views/AdminSettingsPublicLinks.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_public_links', requiresRole: 'admin' },
        },
        {
          path: 'settings/share-approval',
          name: 'admin-settings-share-approval',
          component: () => import('@/views/AdminSettingsShareApproval.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_share_approval', requiresRole: 'admin' },
        },
        {
          path: 'settings/email',
          name: 'admin-settings-email',
          component: () => import('@/views/AdminSettingsEmail.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_email', requiresRole: 'admin' },
        },
        {
          path: 'settings/email-templates',
          name: 'admin-settings-email-templates',
          component: () => import('@/views/AdminSettingsEmailTemplates.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_email_templates', requiresRole: 'admin' },
        },
        {
          path: 'settings/imap',
          name: 'admin-settings-imap',
          component: () => import('@/views/AdminSettingsImap.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_imap', requiresRole: 'admin' },
        },
        {
          path: 'settings/general',
          name: 'admin-settings-general',
          component: () => import('@/views/AdminSettingsGeneral.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_general', requiresRole: 'admin' },
        },
        {
          path: 'settings/branding',
          name: 'admin-settings-branding',
          component: () => import('@/views/AdminSettingsBranding.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_branding', requiresRole: 'admin' },
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
          meta: { density: 'operator', titleKey: 'admin_settings_twofa', requiresRole: 'admin' },
        },
        {
          path: 'settings/quarantine',
          name: 'admin-settings-quarantine',
          component: () => import('@/views/AdminSettingsQuarantine.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_quarantine', requiresRole: 'admin' },
        },
        {
          path: 'settings/email-change',
          name: 'admin-settings-email-change',
          component: () => import('@/views/AdminSettingsEmailChange.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_email_change', requiresRole: 'admin' },
        },
        {
          path: 'settings/scan-guard',
          name: 'admin-settings-scan-guard',
          component: () => import('@/views/AdminSettingsScanGuard.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_scan_guard', requiresRole: 'admin' },
        },
        {
          // The scan guard's POLICY lives on the page above; what it is
          // currently doing - and undoing it - lives here.
          path: 'ip-blocks',
          name: 'admin-ip-blocks',
          component: () => import('@/views/AdminIpBlocks.vue'),
          meta: { density: 'operator', titleKey: 'admin_ip_blocks', requiresRole: 'admin' },
        },
        {
          path: 'settings/error-alerts',
          name: 'admin-settings-error-alerts',
          component: () => import('@/views/AdminSettingsErrorAlerts.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_error_alerts', requiresRole: 'admin' },
        },
        {
          path: 'settings/advanced',
          name: 'admin-settings-advanced',
          component: () => import('@/views/AdminSettingsAdvanced.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_advanced', requiresRole: 'admin' },
        },
        {
          path: 'settings/backup',
          name: 'admin-settings-backup',
          component: () => import('@/views/AdminSettingsBackup.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_backup', requiresRole: 'admin' },
        },
        {
          path: 'settings/maintenance',
          name: 'admin-settings-maintenance',
          component: () => import('@/views/AdminSettingsMaintenance.vue'),
          meta: { density: 'operator', titleKey: 'admin_settings_maintenance', requiresRole: 'admin' },
        },
        {
          path: 'system',
          name: 'admin-system',
          component: () => import('@/views/AdminSystem.vue'),
          meta: { density: 'operator', titleKey: 'admin_system', requiresRole: 'admin' },
        },
        {
          path: 'scheduled-tasks',
          name: 'admin-scheduled-tasks',
          component: () => import('@/views/AdminScheduledTasks.vue'),
          meta: { density: 'operator', titleKey: 'admin_scheduled_tasks', requiresRole: 'admin' },
        },
      ],
    },
    {
      // Legacy notice page - superseded by auto-launch on /account/2fa
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
      meta: { public: true, density: 'editorial', titleKey: 'public_share' },
    },

    /* anonymous "manage subscriptions" page (email footer links) ----------- */
    {
      path: '/manage-notifications/:token',
      name: 'manage-notifications',
      component: () => import('@/views/ManageNotifications.vue'),
      meta: { public: true, density: 'editorial', titleKey: 'manage_notifications' },
    },

    /* legal pages (footer links; mandatory in much of the EU) -------------- */
    {
      path: '/imprint',
      name: 'imprint',
      component: () => import('@/views/LegalPage.vue'),
      meta: { public: true, density: 'editorial', titleKey: 'imprint' },
    },
    {
      path: '/privacy',
      name: 'privacy',
      component: () => import('@/views/LegalPage.vue'),
      meta: { public: true, density: 'editorial', titleKey: 'privacy' },
    },

    /* fallback ------------------------------------------------------------- */
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFound.vue'),
      meta: { public: true, density: 'editorial', titleKey: 'not_found' },
    },
  ],
})

/** Minimal auth shape the guard needs - lets navigationGuard be unit-tested
 * without a live Pinia store. The real store satisfies this structurally. */
export interface GuardAuthState {
  setupRequired: boolean
  isAuthenticated: boolean
  user: MeResponse | null
}

/** Pure navigation-guard decision: given the target route + resolved auth
 * state, return a redirect target or `undefined` to allow. Extracted from the
 * beforeEach closure so the redirect logic is unit-testable (the async
 * bootstrap stays in the registration below). */
export function navigationGuard(
  to: RouteLocationNormalized,
  auth: GuardAuthState,
): RouteLocationRaw | undefined {
  // v1.0.0 first-install: if no admin exists, every route bounces to /setup;
  // the setup route 404s once an admin exists.
  if (auth.setupRequired && to.name !== 'setup') return { name: 'setup' }
  if (!auth.setupRequired && to.name === 'setup') return { name: 'login' }

  // Auth gate: non-public route + logged-out → /login (carry the target).
  if (to.meta.public !== true && !auth.isAuthenticated) {
    return {
      name: 'login',
      query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined,
    }
  }

  // Back from an SSO round-trip: the backend callback always lands on `/`, so
  // the deep link Login.vue stashed before leaving is consumed here. Once, and
  // only for an authenticated arrival at the landing route (audit 2026-07-30,
  // fe-auth-7).
  if (to.name === 'home' && auth.isAuthenticated) {
    const stashed = takePostLoginRedirect()
    if (stashed && stashed !== to.fullPath) return { path: stashed }
  }

  // Logged-in users hitting /login → bounce to their effective landing
  // (avoids a double form on F5).
  if (to.name === 'login' && auth.isAuthenticated) {
    return { path: effectiveLandingPath(auth.user) }
  }

  // Admin-only routes - bounce non-admins to their effective landing.
  if (
    to.meta.requiresRole === 'admin' &&
    auth.isAuthenticated &&
    auth.user?.role !== 'admin'
  ) {
    return { path: effectiveLandingPath(auth.user) }
  }

  // Hitting `/` while admin disabled the home page → redirect forward.
  if (
    to.name === 'home' &&
    auth.isAuthenticated &&
    auth.user?.home_page_enabled === false
  ) {
    return { path: effectiveLandingPath(auth.user) }
  }

  // Forced-2FA: policy applies + TOTP not enabled → into the setup wizard,
  // carrying the original destination as `?redirect=` (avoid a loop when
  // already on the 2FA route or /login).
  if (
    auth.isAuthenticated &&
    auth.user?.requires_2fa === true &&
    to.name !== 'login' &&
    to.meta.public !== true &&
    !to.path.startsWith('/account/2fa')
  ) {
    return {
      name: 'account-2fa',
      query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined,
    }
  }

  return undefined
}

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  // Wait for the silent-refresh bootstrap. main.ts kicked it off before mount;
  // bootstrap() returns the cached in-flight promise so every guard call awaits
  // the same resolution, then it's a no-op.
  await auth.bootstrap()
  return navigationGuard(to, {
    setupRequired: auth.setupRequired,
    isAuthenticated: auth.isAuthenticated,
    user: auth.user,
  })
})

// Localized document title. Every route's title was a hardcoded English
// string, so a German user's browser tab, history entry and bookmark were
// always in English - and the tab title is one of the few strings a user sees
// without looking at the page (audit 2026-07-30, fe-i18n-a11y-3). The route
// carries a KEY now; the string is resolved at navigation time, so it also
// follows an in-session language switch.
router.afterEach((to) => {
  const key = to.meta.titleKey as string | undefined
  const brand = 'file:Heron'
  if (!key || key === 'home') {
    document.title = brand
    return
  }
  const label = i18n.global.t(`page_title.${key}`)
  document.title = label === `page_title.${key}` ? brand : `${label} · ${brand}`
})

export default router
