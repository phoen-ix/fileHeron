<script setup lang="ts">
  /* /secrets/requests/:id - one secret request (v2.24.0).
   *
   * The requester (and an admin) sees whom it went to, its state, and - once
   * answered - a way to the answer, which only the requester can open. Someone
   * who was asked sees what is wanted and answers here. Nobody sees an answer's
   * text on this page: it is an ordinary secret, opened on its own page. */
  import { computed, onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'
  import { useRoute } from 'vue-router'

  import {
    answerSecretRequest,
    cancelSecretRequest,
    getSecretRequest,
    getSecretRequestLinks,
  } from '@/api/secretRequests'
  import SecretAnswerForm from '@/components/SecretAnswerForm.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
  import { useUiStore } from '@/stores/ui'
  import type { SecretRequestLinkItem, SecretRequestResponse } from '@/types/api'
  import { secretRequestPill, secretRequestStateKey } from '@/utils/statePill'

  const route = useRoute()
  const ui = useUiStore()
  const { t } = useI18n()
  const { describe } = useApiError()
  const { formatDate, formatExpiry } = useSiteDateFormat()

  const req = ref<SecretRequestResponse | null>(null)
  const loading = ref(true)
  const errorMsg = ref<string | null>(null)
  const cancelling = ref(false)
  const answered = ref(false)
  const links = ref<SecretRequestLinkItem[] | null>(null)
  const copiedId = ref<number | null>(null)

  const id = computed(() => String(route.params.id))
  const isRequester = computed(() => req.value?.viewer_role === 'requester')
  const isTarget = computed(() => req.value?.viewer_role === 'target')
  const open = computed(() => req.value?.closed_reason === null)
  const stateKey = computed(() => (req.value ? secretRequestStateKey(req.value) : 'open'))

  async function load() {
    errorMsg.value = null
    try {
      const { data } = await getSecretRequest(id.value)
      req.value = data
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      loading.value = false
    }
  }

  async function submitAnswer(content: string, passphrase: string | null) {
    await answerSecretRequest(id.value, content, passphrase)
  }

  function onAnswered() {
    answered.value = true
    void load()
  }

  const termsText = computed(() => {
    const r = req.value
    if (!r) return ''
    const parts: string[] = []
    if (r.answer_max_views != null)
      parts.push(t('secret_requests.terms.views', { n: r.answer_max_views }, r.answer_max_views))
    if (r.answer_expires_in_sec != null)
      parts.push(
        t(
          'secret_requests.terms.lifetime',
          { n: Math.max(1, Math.round(r.answer_expires_in_sec / 86400)) },
          Math.max(1, Math.round(r.answer_expires_in_sec / 86400)),
        ),
      )
    return parts.join(' · ')
  })

  const answeredByText = computed(() => {
    const r = req.value
    if (!r || !r.answered_via) return ''
    if (r.answered_by) return r.answered_by.display_name
    if (r.answered_via === 'email') return r.answered_by_email ?? t('secret_requests.via.email')
    return t('secret_requests.via.link')
  })

  async function onCancel() {
    if (!req.value) return
    const ok = await ui.confirm({
      title: t('secret_requests.detail.cancel_title'),
      message: t('secret_requests.detail.cancel_confirm'),
      confirmLabel: t('secret_requests.detail.cancel'),
      danger: true,
    })
    if (!ok) return
    cancelling.value = true
    try {
      const { data } = await cancelSecretRequest(req.value.id)
      req.value = data
      links.value = null
      ui.pushToast(t('secret_requests.detail.cancelled_toast'), 'success')
    } catch (err) {
      ui.pushToast(describe(err), 'error')
    } finally {
      cancelling.value = false
    }
  }

  async function showLinks() {
    try {
      const { data } = await getSecretRequestLinks(id.value)
      links.value = data.items
    } catch (err) {
      ui.pushToast(describe(err), 'error')
    }
  }

  async function copy(item: SecretRequestLinkItem) {
    if (!item.url) return
    try {
      await navigator.clipboard.writeText(item.url)
      copiedId.value = item.target_id
      window.setTimeout(() => (copiedId.value = null), 1600)
    } catch {
      /* clipboard blocked - the link is on screen to select */
    }
  }

  function targetText(tg: NonNullable<SecretRequestResponse['targets']>[number]): string {
    if (tg.user) return tg.user.display_name
    if (tg.group) return tg.group.name
    if (tg.email) return tg.email
    return t('secret_requests.via.link')
  }

  onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <RouterLink :to="{ name: 'secrets', query: { box: 'requests' } }" class="back">
      ← {{ t('secret_requests.detail.back') }}
    </RouterLink>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

    <template v-else-if="req">
      <span class="fh-eyebrow">{{ t('secret_requests.eyebrow') }}</span>
      <div class="title-row">
        <h1 class="fh-display-md">{{ req.label }}</h1>
        <span class="fh-pill" :data-state="secretRequestPill(stateKey)">
          {{ t(`secret_request_state.${stateKey}`) }}
        </span>
      </div>
      <p v-if="req.note" class="note" data-testid="request-note-text">{{ req.note }}</p>

      <dl class="kvs">
        <div class="kv">
          <dt class="kv-label">{{ t('secret_requests.detail.asked_by') }}</dt>
          <dd class="kv-value">{{ req.requester.display_name }}</dd>
        </div>
        <div class="kv">
          <dt class="kv-label">{{ t('secret_requests.detail.open_until') }}</dt>
          <dd class="kv-value fh-mono">{{ formatExpiry(req.expires_at) }}</dd>
        </div>
        <div class="kv">
          <dt class="kv-label">{{ t('secret_requests.detail.terms') }}</dt>
          <dd class="kv-value">{{ termsText }}</dd>
        </div>
        <!-- The requester's own passphrase - not something the person asked has
             to know about beyond the answer form's note. -->
        <div v-if="!isTarget" class="kv">
          <dt class="kv-label">
            {{
              isRequester
                ? t('secret_requests.detail.passphrase')
                : t('secret_requests.detail.passphrase_requester')
            }}
          </dt>
          <dd class="kv-value">{{ req.has_passphrase ? t('common.yes') : t('common.no') }}</dd>
        </div>
        <div v-if="req.fulfilled_at" class="kv">
          <dt class="kv-label">{{ t('secret_requests.detail.answered') }}</dt>
          <dd class="kv-value">
            <span class="fh-mono">{{ formatDate(req.fulfilled_at) }}</span>
            <span v-if="answeredByText"> · {{ answeredByText }}</span>
          </dd>
        </div>
      </dl>

      <!-- Someone who was asked: the answer form, or why there is none. -->
      <section v-if="isTarget" class="answer-section">
        <hr class="fh-rule" />
        <p v-if="answered" class="fh-notice" data-tone="accent" data-testid="answer-sent">
          {{ t('secret_requests.answer.sent', { name: req.requester.display_name }) }}
        </p>
        <SecretAnswerForm
          v-else-if="req.can_answer"
          :requester-name="req.requester.display_name"
          :has-request-passphrase="req.has_passphrase"
          :submit="submitAnswer"
          @sent="onAnswered"
        />
        <p v-else class="fh-notice" data-testid="request-closed">
          {{ t(`secret_requests.closed.${req.closed_reason ?? 'fulfilled'}`) }}
        </p>
      </section>

      <template v-else>
        <div class="actions-row">
          <RouterLink
            v-if="isRequester && req.answer_secret_id"
            :to="{ name: 'secret-detail', params: { id: req.answer_secret_id } }"
            class="fh-btn"
            data-testid="open-answer"
          >
            {{ t('secret_requests.detail.open_answer') }} <span aria-hidden="true">→</span>
          </RouterLink>
          <button
            v-if="open"
            type="button"
            class="fh-btn-text danger"
            :disabled="cancelling"
            data-testid="cancel-request"
            @click="onCancel"
          >
            {{ cancelling ? t('common.loading') : t('secret_requests.detail.cancel') }}
          </button>
        </div>

        <section
          v-if="isRequester && open && (req.target_summary?.emails || req.target_summary?.link)"
          class="links-section"
        >
          <h2 class="section-h2">{{ t('secret_requests.detail.links_title') }}</h2>
          <p class="fh-field-help">{{ t('secret_requests.detail.links_help') }}</p>
          <button
            v-if="links === null"
            type="button"
            class="fh-btn-text"
            data-testid="show-request-links"
            @click="showLinks"
          >
            {{ t('secrets.detail.show_links') }}
          </button>
          <ul v-else class="link-list">
            <li v-for="item in links" :key="item.target_id" class="link-item">
              <span class="fh-eyebrow">{{
                item.email ? item.email : t('secret_requests.via.link')
              }}</span>
              <pre v-if="item.url" class="link fh-mono">{{ item.url }}</pre>
              <button v-if="item.url" type="button" class="fh-btn-text" @click="copy(item)">
                {{ copiedId === item.target_id ? t('secrets.copied') : t('secrets.copy_link') }}
              </button>
            </li>
          </ul>
        </section>

        <h2 class="section-h2">{{ t('secret_requests.detail.asked_title') }}</h2>
        <div class="fh-table-scroll">
          <table class="target-table">
            <thead>
              <tr>
                <th>{{ t('secret_requests.detail.col_target') }}</th>
                <th>{{ t('secret_requests.detail.col_kind') }}</th>
                <th>{{ t('secret_requests.detail.col_mailed') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="tg in req.targets ?? []" :key="tg.id">
                <td>{{ targetText(tg) }}</td>
                <td class="fh-mono">{{ t(`secrets.kind.${tg.kind}`) }}</td>
                <td class="fh-mono">{{ tg.notified_at ? formatDate(tg.notified_at) : '-' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
  .back {
    display: block;
    margin-bottom: var(--fh-space-3);
    color: var(--fh-subtle);
    text-decoration: none;
  }

  .loading {
    color: var(--fh-subtle);
    padding: var(--fh-space-5) 0;
  }

  .title-row {
    display: flex;
    gap: var(--fh-space-3);
    align-items: center;
    flex-wrap: wrap;
  }

  .note {
    max-width: 64ch;
    white-space: pre-wrap;
    color: var(--fh-ink-soft);
  }

  .kvs {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: var(--fh-space-3);
    margin: var(--fh-space-4) 0;
  }

  .kv-label {
    font-family: var(--fh-font-mono);
    font-size: var(--fh-text-mono-sm);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--fh-subtle);
  }

  .kv-value {
    margin: 0;
  }

  .answer-section {
    max-width: 720px;
  }

  .actions-row {
    display: flex;
    gap: var(--fh-space-3);
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: var(--fh-space-3);
  }

  .section-h2 {
    font-family: var(--fh-font-display);
    font-size: 1.25rem;
    font-weight: 400;
    margin: var(--fh-space-4) 0 var(--fh-space-2);
  }

  .links-section {
    max-width: 720px;
  }

  .link-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-3);
  }

  .link {
    margin: var(--fh-space-1) 0;
    padding: var(--fh-space-2) var(--fh-space-3);
    background: var(--fh-paper-raised);
    border: var(--fh-border);
    border-radius: var(--fh-radius-sm);
    word-break: break-all;
    white-space: pre-wrap;
    user-select: all;
  }

  .target-table {
    width: 100%;
    border-collapse: collapse;
  }

  .target-table th,
  .target-table td {
    text-align: left;
    padding: var(--fh-space-2) var(--fh-space-3);
    border-bottom: 1px solid var(--fh-rule);
  }

  .target-table th {
    font-family: var(--fh-font-mono);
    font-size: var(--fh-text-mono-sm);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--fh-subtle);
    font-weight: 500;
  }

  .fh-btn-text.danger {
    color: var(--fh-danger);
  }
</style>
