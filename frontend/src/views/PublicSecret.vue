<script setup lang="ts">
  /* /s#<token> - a secret for someone without an account (v2.24.0).
   *
   * The token is read from the URL FRAGMENT, which the browser never sends: it
   * reaches no proxy, no access log and no Referer. It goes to the API in a
   * POST body. Loading this page costs nothing - mail gateways open links
   * before people do - and only the Reveal button uses a view. */
  import { computed, onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'
  import { useRoute } from 'vue-router'

  import { asEnvelope } from '@/api/client'
  import { peekSecret, revealPublicSecret } from '@/api/secrets'
  import BrandLogo from '@/components/BrandLogo.vue'
  import BrandMark from '@/components/BrandMark.vue'
  import SecretReveal from '@/components/SecretReveal.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
  import { useSiteStore } from '@/stores/site'
  import type { PublicSecretPeekResponse } from '@/types/api'

  const route = useRoute()
  const site = useSiteStore()
  const { t } = useI18n()
  const { describe } = useApiError()
  const { formatDate, formatExpiry } = useSiteDateFormat()

  const showBrand = computed(() => site.branding.show_public)
  const showLogo = computed(() => showBrand.value && !!site.branding.logo_url)

  const token = computed(() => route.hash.replace(/^#/, ''))
  const peek = ref<PublicSecretPeekResponse | null>(null)
  const loading = ref(true)
  const errorMsg = ref<string | null>(null)
  const errorCode = ref<string | null>(null)
  const revealed = ref(false)

  async function load() {
    loading.value = true
    errorMsg.value = null
    errorCode.value = null
    if (!token.value) {
      errorCode.value = 'INCOMPLETE'
      errorMsg.value = t('public_secret.incomplete')
      loading.value = false
      return
    }
    try {
      const { data } = await peekSecret(token.value)
      peek.value = data
    } catch (err) {
      errorCode.value = asEnvelope(err)?.code ?? null
      errorMsg.value = describe(err)
    } finally {
      loading.value = false
    }
  }

  async function reveal(passphrase: string | null) {
    const { data } = await revealPublicSecret(token.value, passphrase)
    return data
  }

  function onFailed(code: string) {
    // Locked, gone, burned: re-read so the page states it rather than leaving a
    // live-looking form behind an error line.
    if (code !== 'SECRET_PASSPHRASE_INVALID' && code !== 'SECRET_RATE_LIMITED') void load()
  }

  onMounted(load)
</script>

<template>
  <div class="fh-prose public-secret">
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

    <span class="fh-eyebrow">{{ t('public_secret.eyebrow') }}</span>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <div v-else-if="errorMsg" class="error-state" data-testid="public-secret-error">
      <h1 class="fh-display-md">{{ t('public_secret.unavailable') }}</h1>
      <p class="fh-field-help">{{ errorMsg }}</p>
    </div>

    <template v-else-if="peek">
      <h1 class="fh-display">
        {{
          peek.label ||
          (peek.requires_passphrase ? t('public_secret.title_protected') : t('public_secret.title'))
        }}
      </h1>
      <p v-if="peek.sender_name" class="from">
        {{ t('public_secret.from', { name: peek.sender_name }) }}
      </p>
      <p class="meta fh-mono">
        <span v-if="peek.expires_at">{{
          t('public_secret.until', { d: formatExpiry(peek.expires_at) })
        }}</span>
        <!-- The count was read before the reveal; afterwards the card says
             what is left, so a stale "1 view left" must not stay up here. -->
        <span v-if="peek.views_left !== null && !revealed">{{
          t('public_secret.views_left', { n: peek.views_left }, peek.views_left)
        }}</span>
      </p>
      <p v-if="!revealed" class="fh-field-help intro">{{ t('public_secret.intro') }}</p>

      <hr class="fh-rule" />

      <p v-if="peek.locked_until" class="fh-notice" role="status" data-tone="accent">
        {{ t('public_secret.locked', { d: formatDate(peek.locked_until) }) }}
      </p>
      <SecretReveal
        v-else
        :requires-passphrase="peek.requires_passphrase"
        :views-left="peek.views_left"
        :attempts-left="peek.attempts_left"
        :reveal="reveal"
        @revealed="revealed = true"
        @failed="onFailed"
      />
    </template>
  </div>
</template>

<style scoped>
  .public-secret {
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

  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: var(--fh-space-4);
    color: var(--fh-subtle);
    font-size: var(--fh-text-mono-sm);
  }

  .intro {
    max-width: 60ch;
  }
</style>
