<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { getMailLogDetail, resendMailLog } from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import type { AdminMailDetail } from '@/types/api'

const { t } = useI18n()
const { formatDate } = useSiteDateFormat()
const { describe } = useApiError()
const ui = useUiStore()
const route = useRoute()
const router = useRouter()

const row = ref<AdminMailDetail | null>(null)
const loading = ref(true)
const errorMsg = ref<string | null>(null)
const resending = ref(false)

function statusTone(s: string): 'active' | 'warn' | 'danger' | undefined {
  if (s === 'sent') return 'active'
  if (s === 'queued') return 'warn'
  if (s === 'failed' || s === 'error') return 'danger'
  return undefined
}

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    const { data } = await getMailLogDetail(Number(route.params.id))
    row.value = data
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

// Render the stored HTML body in a SANDBOXED iframe, never a Blob tab.
//
// The previous implementation Blob-wrapped the body and window.open()'d it,
// under the belief that a top-level navigation is "a separate origin" and so is
// constrained by the SPA's CSP. Both halves were wrong: a blob: URL INHERITS the
// creating document's origin, and the SPA ships no CSP at all (nginx.conf omits
// it; the backend middleware only decorates its own responses). The opened tab
// was therefore same-origin and could call /api/auth/refresh with the httpOnly
// cookie - i.e. a stored mail body could take over the admin session
// (audit 2026-07-30).
//
// `sandbox=""` denies every capability including scripts and same-origin, which
// is what the two sibling viewers (AdminInboxDetail, AdminSettingsEmailTemplates)
// already do.
const showHtml = ref(false)

async function onResend() {
  if (!row.value || !row.value.can_resend) return
  resending.value = true
  try {
    const { data } = await resendMailLog(row.value.id)
    ui.pushToast(t('admin_mail.detail.resend_ok'), 'success')
    await router.push({ name: 'admin-mail-detail', params: { id: data.new_log_id } })
  } catch (err) {
    ui.pushToast(describe(err), 'error')
  } finally {
    resending.value = false
  }
}

watch(() => route.params.id, load)
onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <RouterLink :to="{ name: 'admin-mail-log' }" class="back-link">
      ← {{ t('admin_mail.detail.back') }}
    </RouterLink>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>

    <template v-else-if="row">
      <div class="detail-head">
        <h1 class="subject">{{ row.subject }}</h1>
        <button
          type="button"
          class="fh-btn"
          :disabled="!row.can_resend || resending"
          @click="onResend"
        >
          {{ resending ? t('common.loading') : t('admin_mail.detail.resend') }}
        </button>
      </div>

      <p v-if="!row.can_resend" class="fh-field-help resend-note">
        {{ t('admin_mail.detail.resend_disabled') }}
      </p>

      <dl class="meta">
        <div>
          <dt>{{ t('admin_mail.detail.status') }}</dt>
          <dd>
            <span class="fh-pill" :data-state="statusTone(row.status)">
              {{ t(`admin_mail.status.${row.status}`) }}
            </span>
            <span v-if="row.smtp_code" class="fh-mono">SMTP {{ row.smtp_code }}</span>
          </dd>
        </div>
        <div>
          <dt>{{ t('admin_mail.detail.recipient') }}</dt>
          <dd class="fh-mono">
            <RouterLink
              v-if="row.recipient_user_id !== null"
              :to="{ name: 'admin-user-detail', params: { id: row.recipient_user_id } }"
            >
              {{ row.recipient_email }}
            </RouterLink>
            <span v-else>{{ row.recipient_email }}</span>
          </dd>
        </div>
        <div>
          <dt>{{ t('admin_mail.detail.when') }}</dt>
          <dd class="fh-mono">{{ formatDate(row.created_at, { second: '2-digit' }) }}</dd>
        </div>
        <div>
          <dt>{{ t('admin_mail.detail.category') }}</dt>
          <dd class="fh-mono">{{ row.category ?? '-' }} / {{ row.via }}</dd>
        </div>
        <div>
          <dt>{{ t('admin_mail.detail.attempts') }}</dt>
          <dd class="fh-mono">{{ row.attempts }}</dd>
        </div>
        <div v-if="row.masked">
          <dt>{{ t('admin_mail.detail.masked') }}</dt>
          <dd><span class="fh-pill" data-state="warn">{{ t('admin_mail.masked') }}</span></dd>
        </div>
        <div v-if="row.error_message" class="full">
          <dt>{{ t('admin_mail.detail.error') }}</dt>
          <dd class="fh-mono err">{{ row.error_class }}: {{ row.error_message }}</dd>
        </div>
      </dl>

      <div class="body-head">
        <h2 class="form-h2">{{ t('admin_mail.detail.body') }}</h2>
        <button
          v-if="row.body_html"
          type="button"
          class="fh-btn fh-btn-ghost"
          @click="showHtml = !showHtml"
        >
          {{ t('admin_mail.detail.open_html') }}
        </button>
      </div>
      <!-- Untrusted stored HTML: sandboxed, never opened as a blob: tab. -->
      <iframe
        v-if="showHtml && row.body_html"
        class="body-frame"
        sandbox=""
        :srcdoc="row.body_html"
        :title="t('admin_mail.detail.open_html')"
      />
      <pre v-else class="body-text fh-mono">{{ row.body_text ?? t('admin_mail.detail.no_body') }}</pre>
    </template>
  </div>
</template>

<style scoped>
.body-frame {
  width: 100%;
  min-height: 24rem;
  border: 1px solid var(--fh-border);
  background: #fff;
}
.back-link {
  display: inline-block;
  margin-bottom: var(--fh-space-3);
  color: var(--fh-subtle);
  text-decoration: none;
  font-size: var(--fh-text-body-sm);
}

.back-link:hover {
  color: var(--fh-accent);
}

.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}

.detail-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--fh-space-4);
}

.subject {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  margin: 0;
}

.resend-note {
  margin-top: var(--fh-space-1);
}

.meta {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--fh-space-3);
  margin: var(--fh-space-4) 0;
}

.meta .full {
  grid-column: 1 / -1;
}

.meta dt {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fh-subtle);
  margin-bottom: 2px;
}

.meta dd {
  margin: 0;
  display: flex;
  gap: var(--fh-space-2);
  align-items: center;
}

.err {
  color: var(--fh-danger);
}

.body-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--fh-space-4);
  margin-top: var(--fh-space-4);
}

.form-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.25rem;
  margin: 0;
}

.body-text {
  margin-top: var(--fh-space-2);
  padding: var(--fh-space-3);
  background: var(--fh-paper-raised);
  border: var(--fh-border);
  border-radius: var(--fh-radius-sm);
  font-size: var(--fh-text-mono-sm);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 32rem;
  overflow: auto;
}
</style>
