<script setup lang="ts">
  /* /r#<token> - answer a secret request without an account (v2.24.0).
   *
   * The token is read from the URL FRAGMENT, which the browser never sends: it
   * reaches no proxy, no access log and no Referer. It goes to the API in a
   * POST body. Loading this page changes nothing - mail gateways open links
   * before people do - and only Send answers. One answer per request. */
  import { computed, onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'
  import { useRoute } from 'vue-router'

  import { asEnvelope } from '@/api/client'
  import { answerPublicSecretRequest, peekSecretRequest } from '@/api/secretRequests'
  import BrandLogo from '@/components/BrandLogo.vue'
  import BrandMark from '@/components/BrandMark.vue'
  import SecretAnswerForm from '@/components/SecretAnswerForm.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
  import { useSiteStore } from '@/stores/site'
  import type { PublicSecretRequestPeekResponse } from '@/types/api'

  const route = useRoute()
  const site = useSiteStore()
  const { t } = useI18n()
  const { describe } = useApiError()
  const { formatExpiry } = useSiteDateFormat()

  const showBrand = computed(() => site.branding.show_public)
  const showLogo = computed(() => showBrand.value && !!site.branding.logo_url)

  const token = computed(() => route.hash.replace(/^#/, ''))
  const peek = ref<PublicSecretRequestPeekResponse | null>(null)
  const loading = ref(true)
  const errorMsg = ref<string | null>(null)
  const closedReason = ref<string | null>(null)
  const sent = ref(false)

  async function load() {
    loading.value = true
    errorMsg.value = null
    closedReason.value = null
    if (!token.value) {
      errorMsg.value = t('public_secret_request.incomplete')
      loading.value = false
      return
    }
    try {
      const { data } = await peekSecretRequest(token.value)
      peek.value = data
    } catch (err) {
      const env = asEnvelope(err)
      if (env?.code === 'SECRET_REQUEST_CLOSED') {
        const reason = (env.details as { reason?: string } | undefined)?.reason
        closedReason.value = reason ?? 'fulfilled'
      }
      errorMsg.value = describe(err)
    } finally {
      loading.value = false
    }
  }

  async function submit(content: string, passphrase: string | null) {
    await answerPublicSecretRequest(token.value, content, passphrase)
  }

  const termsText = computed(() => {
    const p = peek.value
    if (!p) return ''
    const parts: string[] = []
    if (p.answer_max_views != null)
      parts.push(t('secret_requests.terms.views', { n: p.answer_max_views }, p.answer_max_views))
    if (p.answer_expires_in_sec != null) {
      const days = Math.max(1, Math.round(p.answer_expires_in_sec / 86400))
      parts.push(t('secret_requests.terms.lifetime', { n: days }, days))
    }
    return parts.join(' · ')
  })

  onMounted(load)
</script>

<template>
  <div class="fh-prose public-request">
    <div v-if="showBrand" class="public-brand">
      <BrandLogo
        v-if="showLogo"
        :src="site.branding.logo_url as string"
        :alt="site.appName"
        :link-url="site.branding.link_url"
        size="sm"
      />
      <BrandMark size="sm" :linkable="false" />
    </div>

    <span class="fh-eyebrow">{{ t('public_secret_request.eyebrow') }}</span>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <div v-else-if="errorMsg" class="error-state" data-testid="public-request-error">
      <h1 class="fh-display-md">
        {{
          closedReason
            ? t('public_secret_request.closed_title')
            : t('public_secret_request.unavailable')
        }}
      </h1>
      <p class="fh-field-help">
        {{ closedReason ? t(`secret_requests.closed.${closedReason}`) : errorMsg }}
      </p>
    </div>

    <template v-else-if="peek">
      <h1 class="fh-display">{{ peek.label }}</h1>
      <p class="from">
        {{
          t('public_secret_request.from', {
            name: peek.requester_name || t('secret_requests.answer.the_requester'),
          })
        }}
      </p>
      <p v-if="peek.note" class="note" data-testid="public-request-note">{{ peek.note }}</p>
      <p class="meta fh-mono">
        <span>{{ t('public_secret_request.until', { d: formatExpiry(peek.expires_at) }) }}</span>
        <span v-if="termsText">{{ t('public_secret_request.terms', { terms: termsText }) }}</span>
      </p>

      <hr class="fh-rule" />

      <p v-if="sent" class="fh-notice" data-tone="accent" data-testid="public-answer-sent">
        {{
          t('secret_requests.answer.sent', {
            name: peek.requester_name || t('secret_requests.answer.the_requester'),
          })
        }}
      </p>
      <SecretAnswerForm
        v-else
        :requester-name="peek.requester_name"
        :has-request-passphrase="peek.has_passphrase"
        :submit="submit"
        @sent="sent = true"
      />
    </template>
  </div>
</template>

<style scoped>
  .public-request {
    max-width: 720px;
    padding-top: var(--fh-space-6);
    padding-bottom: var(--fh-space-6);
  }

  .public-brand {
    display: flex;
    align-items: center;
    gap: var(--fh-space-2);
    margin-bottom: var(--fh-space-5);
  }

  .loading {
    color: var(--fh-subtle);
    padding: var(--fh-space-5) 0;
  }

  .error-state {
    padding: var(--fh-space-4) 0;
  }

  .from {
    margin: 0;
    color: var(--fh-ink-soft);
  }

  .note {
    white-space: pre-wrap;
    max-width: 60ch;
  }

  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: var(--fh-space-4);
    color: var(--fh-subtle);
    font-size: var(--fh-text-mono-sm);
  }
</style>
