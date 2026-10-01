<script setup lang="ts">
  /* /secrets/:id (v2.24.0). A recipient reveals the secret here. The sender
   * and admins see its status instead - who it went to, who viewed it when,
   * what is left - and may burn it early; neither can read it. The sender may
   * copy their own links again (each read is audited server-side). */
  import { computed, onMounted, ref } from 'vue'
  import { useI18n } from 'vue-i18n'
  import { useRoute } from 'vue-router'

  import {
    burnSecret,
    getSecret,
    getSecretLinks,
    removeSecretLink,
    replaceSecretLink,
    revealSecret,
  } from '@/api/secrets'
  import SecretReveal from '@/components/SecretReveal.vue'
  import { useApiError } from '@/composables/useApiError'
  import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
  import { useAuthStore } from '@/stores/auth'
  import { useUiStore } from '@/stores/ui'
  import type {
    SecretEvent,
    SecretLinkItem,
    SecretRecipientStatus,
    SecretResponse,
  } from '@/types/api'
  import { secretStatePill } from '@/utils/statePill'

  const route = useRoute()
  const auth = useAuthStore()
  const ui = useUiStore()
  const { t } = useI18n()
  const { describe } = useApiError()
  const { formatDate, formatExpiry } = useSiteDateFormat()

  const secret = ref<SecretResponse | null>(null)
  const loading = ref(true)
  const errorMsg = ref<string | null>(null)
  const burning = ref(false)
  const links = ref<SecretLinkItem[] | null>(null)
  const linkBusy = ref(false)
  const copiedId = ref<number | null>(null)

  const id = computed(() => String(route.params.id))
  const isSender = computed(() => secret.value?.viewer_role === 'sender')
  const isRecipient = computed(() => secret.value?.viewer_role === 'recipient')
  const showsRoster = computed(() => isSender.value || auth.user?.role === 'admin')
  const active = computed(() => secret.value?.state === 'active')
  const canManageLink = computed(
    () => isSender.value && active.value && auth.user?.can_send_secrets_external === true,
  )

  async function load() {
    errorMsg.value = null
    try {
      const { data } = await getSecret(id.value)
      secret.value = data
    } catch (err) {
      errorMsg.value = describe(err)
    } finally {
      loading.value = false
    }
  }

  async function reveal(passphrase: string | null) {
    const { data } = await revealSecret(id.value, passphrase)
    return data
  }

  const revealedHere = ref(false)
  function onRevealed() {
    revealedHere.value = true
    void load()
  }

  /** Why a recipient cannot reveal it, in words - or null when they can. */
  const notRevealable = computed<string | null>(() => {
    const s = secret.value
    if (!s || !isRecipient.value || s.can_reveal) return null
    if (s.state !== 'active') return t(`secrets.detail.ended.${s.state}`)
    if (s.burned_for_me) return t('secrets.detail.burned_for_me')
    if (!s.still_recipient) return t('secrets.detail.not_recipient')
    if (s.my_views_left === 0) return t('secrets.detail.no_views_left')
    return t('secrets.detail.ended.expired')
  })

  async function onBurn() {
    if (!secret.value) return
    const ok = await ui.confirm({
      title: t('secrets.detail.burn_title'),
      message: t('secrets.detail.burn_confirm'),
      confirmLabel: t('secrets.detail.burn'),
      danger: true,
    })
    if (!ok) return
    burning.value = true
    try {
      const { data } = await burnSecret(secret.value.id)
      secret.value = data
      links.value = null
      ui.pushToast(t('secrets.detail.burned_toast'), 'success')
    } catch (err) {
      ui.pushToast(describe(err), 'error')
    } finally {
      burning.value = false
    }
  }

  async function onShowLinks() {
    linkBusy.value = true
    try {
      const { data } = await getSecretLinks(id.value)
      links.value = data.items
    } catch (err) {
      ui.pushToast(describe(err), 'error')
    } finally {
      linkBusy.value = false
    }
  }

  async function onReplaceLink() {
    const hasLink = !!secret.value?.recipient_summary?.link
    if (
      hasLink &&
      !(await ui.confirm({ message: t('secrets.detail.replace_confirm'), danger: true }))
    )
      return
    linkBusy.value = true
    try {
      await replaceSecretLink(id.value)
      await load()
      await onShowLinks()
    } catch (err) {
      ui.pushToast(describe(err), 'error')
    } finally {
      linkBusy.value = false
    }
  }

  async function onRemoveLink() {
    if (!(await ui.confirm({ message: t('secrets.detail.remove_confirm'), danger: true }))) return
    linkBusy.value = true
    try {
      await removeSecretLink(id.value)
      links.value = null
      await load()
    } catch (err) {
      ui.pushToast(describe(err), 'error')
    } finally {
      linkBusy.value = false
    }
  }

  async function copy(item: SecretLinkItem) {
    if (!item.url) return
    try {
      await navigator.clipboard.writeText(item.url)
      copiedId.value = item.recipient_id
      window.setTimeout(() => (copiedId.value = null), 1600)
    } catch {
      /* clipboard blocked - the link is on screen to select */
    }
  }

  function recipientName(r: SecretRecipientStatus): string {
    if (r.kind === 'user') return r.user?.display_name ?? '-'
    if (r.kind === 'group') return r.group?.name ?? '-'
    if (r.kind === 'email') return r.email ?? '-'
    return t('secrets.detail.the_link')
  }

  function recipientStatus(r: SecretRecipientStatus): string {
    if (r.revoked) return t('secrets.detail.status.revoked')
    if (r.burned) return t('secrets.detail.status.burned')
    if (r.locked_until) return t('secrets.detail.status.locked', { d: formatDate(r.locked_until) })
    if (r.kind === 'email')
      return r.emailed_at
        ? t('secrets.detail.status.emailed', { d: formatDate(r.emailed_at) })
        : t('secrets.detail.status.not_emailed')
    return ''
  }

  function viewsText(used: number, left: number | null): string {
    return left == null
      ? t('secrets.detail.views_used', { n: used }, used)
      : t('secrets.detail.views_used_left', { n: used, left })
  }

  function eventWho(e: SecretEvent): string {
    if (e.user) return e.user.display_name
    if (e.kind === 'email') return e.email ?? '-'
    if (e.kind === 'link') return t('secrets.detail.the_link')
    return '-'
  }

  const limitsText = computed(() => {
    const s = secret.value
    if (!s) return ''
    if (s.max_views == null) return t('secrets.detail.no_view_limit')
    return t(`secrets.detail.limit.${s.view_scope}`, { n: s.max_views }, s.max_views)
  })

  onMounted(load)
</script>

<template>
  <div class="fh-page" data-density="operator">
    <RouterLink :to="{ name: 'secrets', query: isSender ? { box: 'sent' } : {} }" class="back">
      ← {{ t('secrets.detail.back') }}
    </RouterLink>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" role="alert" data-tone="error">{{ errorMsg }}</div>

    <template v-else-if="secret">
      <span class="fh-eyebrow">{{ t('secrets.eyebrow') }}</span>
      <div class="title-row">
        <h1 class="fh-display-md">{{ secret.label || t('secrets.no_label') }}</h1>
        <span class="fh-pill" :data-state="secretStatePill(secret.state)">
          {{ t(`secret_state.${secret.state}`) }}
        </span>
      </div>

      <dl class="kvs">
        <div class="kv">
          <dt class="kv-label">{{ t('secrets.detail.from') }}</dt>
          <dd class="kv-value">{{ secret.sender.display_name }}</dd>
        </div>
        <div class="kv">
          <dt class="kv-label">{{ t('secrets.detail.sent') }}</dt>
          <dd class="kv-value fh-mono">{{ formatDate(secret.created_at) }}</dd>
        </div>
        <div class="kv">
          <dt class="kv-label">{{ t('secrets.detail.expires') }}</dt>
          <dd class="kv-value fh-mono">{{ formatExpiry(secret.expires_at) }}</dd>
        </div>
        <div class="kv">
          <dt class="kv-label">{{ t('secrets.detail.limit_label') }}</dt>
          <dd class="kv-value">{{ limitsText }}</dd>
        </div>
        <div class="kv">
          <dt class="kv-label">{{ t('secrets.detail.passphrase') }}</dt>
          <dd class="kv-value">
            {{
              !secret.has_passphrase
                ? t('common.no')
                : secret.burn_after_failures
                  ? t('secrets.detail.passphrase_burn', { n: secret.burn_after_failures })
                  : t('common.yes')
            }}
          </dd>
        </div>
        <div v-if="secret.ended_at" class="kv">
          <dt class="kv-label">{{ t('secrets.detail.ended_at') }}</dt>
          <dd class="kv-value fh-mono">{{ formatDate(secret.ended_at) }}</dd>
        </div>
      </dl>

      <section v-if="isRecipient" class="reveal-section">
        <hr class="fh-rule" />
        <!-- Kept mounted once it has revealed: the metadata reload after the
             last view turns `can_reveal` false, and swapping the card out then
             would unmount it - and the text the reader has just revealed. -->
        <SecretReveal
          v-if="secret.can_reveal || revealedHere"
          :requires-passphrase="secret.has_passphrase"
          :views-left="secret.my_views_left ?? null"
          :attempts-left="
            secret.burn_after_failures
              ? Math.max(0, secret.burn_after_failures - (secret.my_failed_attempts ?? 0))
              : null
          "
          :reveal="reveal"
          @revealed="onRevealed"
          @failed="load"
        />
        <p v-else class="fh-notice" data-testid="not-revealable">{{ notRevealable }}</p>
      </section>

      <p v-else class="fh-field-help sender-note">
        {{ isSender ? t('secrets.detail.sender_note') : t('secrets.detail.admin_note') }}
      </p>

      <template v-if="showsRoster">
        <div v-if="active" class="actions-row">
          <button
            type="button"
            class="fh-btn fh-btn-danger"
            :disabled="burning"
            data-testid="burn-now"
            @click="onBurn"
          >
            {{ burning ? t('common.loading') : t('secrets.detail.burn') }}
          </button>
        </div>

        <section v-if="isSender && active" class="links-section">
          <h2 class="section-h2">{{ t('secrets.detail.links_title') }}</h2>
          <p class="fh-field-help">{{ t('secrets.detail.links_help') }}</p>
          <div class="actions-row">
            <button
              type="button"
              class="fh-btn-text"
              :disabled="linkBusy"
              data-testid="show-links"
              @click="onShowLinks"
            >
              {{ t('secrets.detail.show_links') }}
            </button>
            <button
              v-if="canManageLink"
              type="button"
              class="fh-btn-text"
              :disabled="linkBusy"
              @click="onReplaceLink"
            >
              {{
                secret.recipient_summary?.link
                  ? t('secrets.detail.replace_link')
                  : t('secrets.detail.create_link')
              }}
            </button>
            <button
              v-if="secret.recipient_summary?.link"
              type="button"
              class="fh-btn-text danger"
              :disabled="linkBusy"
              @click="onRemoveLink"
            >
              {{ t('secrets.detail.remove_link') }}
            </button>
          </div>
          <ul v-if="links" class="link-list">
            <li v-if="!links.length" class="fh-field-help">{{ t('secrets.detail.no_links') }}</li>
            <li v-for="item in links" :key="item.recipient_id" class="link-item">
              <span class="kv-label">{{
                item.kind === 'email' ? item.email : t('secrets.detail.the_link')
              }}</span>
              <pre v-if="item.url" class="link fh-mono">{{ item.url }}</pre>
              <span v-else class="fh-field-help">{{ t('secrets.detail.link_unavailable') }}</span>
              <button v-if="item.url" type="button" class="fh-btn-text" @click="copy(item)">
                {{ copiedId === item.recipient_id ? t('secrets.copied') : t('secrets.copy_link') }}
              </button>
              <!-- eslint-disable-next-line vue/no-v-html -- server-rendered, deterministic QR SVG of our own link (no user input) -->
              <div v-if="item.qr_svg && item.kind === 'link'" class="qr" v-html="item.qr_svg" />
            </li>
          </ul>
        </section>

        <section class="roster">
          <h2 class="section-h2">{{ t('secrets.detail.recipients_title') }}</h2>
          <div class="fh-table-scroll">
            <table class="detail-table">
              <thead>
                <tr>
                  <th>{{ t('secrets.detail.col.recipient') }}</th>
                  <th>{{ t('secrets.detail.col.views') }}</th>
                  <th>{{ t('secrets.detail.col.status') }}</th>
                </tr>
              </thead>
              <tbody>
                <template v-for="r in secret.recipients ?? []" :key="r.id">
                  <tr>
                    <td>
                      <span class="fh-mono kind">{{ t(`secrets.kind.${r.kind}`) }}</span>
                      {{ recipientName(r) }}
                    </td>
                    <td class="fh-mono">{{ viewsText(r.views_used, r.views_left) }}</td>
                    <td>{{ recipientStatus(r) }}</td>
                  </tr>
                  <tr v-for="m in r.members ?? []" :key="`${r.id}-${m.user.id}`" class="member-row">
                    <td class="member-name">
                      {{ m.user.display_name }}
                      <span v-if="!m.eligible" class="fh-mono row-hint">{{
                        t('secrets.detail.left_group')
                      }}</span>
                    </td>
                    <td class="fh-mono">
                      {{ t('secrets.detail.views_used', { n: m.views_used }, m.views_used) }}
                    </td>
                    <td>{{ m.burned ? t('secrets.detail.status.burned') : '' }}</td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
        </section>

        <section class="activity">
          <h2 class="section-h2">{{ t('secrets.detail.activity_title') }}</h2>
          <p v-if="!(secret.events ?? []).length" class="fh-field-help">
            {{ t('secrets.detail.no_activity') }}
          </p>
          <div v-else class="fh-table-scroll">
            <table class="detail-table">
              <thead>
                <tr>
                  <th>{{ t('secrets.detail.col.when') }}</th>
                  <th>{{ t('secrets.detail.col.what') }}</th>
                  <th>{{ t('secrets.detail.col.who') }}</th>
                  <th>{{ t('secrets.detail.col.ip') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(e, i) in secret.events" :key="i">
                  <td class="fh-mono">{{ formatDate(e.at) }}</td>
                  <td>{{ t(`secrets.outcome.${e.outcome}`) }}</td>
                  <td>{{ eventWho(e) }}</td>
                  <td class="fh-mono">{{ e.ip ?? '' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
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
    align-items: baseline;
    flex-wrap: wrap;
  }

  .kvs {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: var(--fh-space-3);
    margin: var(--fh-space-3) 0;
  }

  .kv {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin: 0;
  }

  .kv-label {
    font-family: var(--fh-font-mono);
    font-size: var(--fh-text-mono-sm);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--fh-subtle);
  }

  .kv-value {
    margin: 0;
    font-size: var(--fh-text-body-md);
    color: var(--fh-ink);
  }

  .reveal-section {
    max-width: 720px;
  }

  .sender-note {
    max-width: 64ch;
  }

  .section-h2 {
    font-family: var(--fh-font-display);
    font-size: 1.25rem;
    font-weight: 400;
    margin: var(--fh-space-4) 0 var(--fh-space-2);
  }

  .actions-row {
    display: flex;
    gap: var(--fh-space-3);
    align-items: center;
    flex-wrap: wrap;
  }

  .link-list {
    list-style: none;
    margin: var(--fh-space-3) 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-3);
  }

  .link-item {
    display: flex;
    flex-direction: column;
    gap: var(--fh-space-1);
    padding: var(--fh-space-3);
    background: var(--fh-paper-raised);
    border: var(--fh-border);
    border-radius: var(--fh-radius-sm);
  }

  .link-item button {
    align-self: flex-start;
  }

  .link {
    margin: 0;
    padding: var(--fh-space-2) var(--fh-space-3);
    background: var(--fh-paper);
    border: var(--fh-border);
    border-radius: var(--fh-radius-sm);
    word-break: break-all;
    white-space: pre-wrap;
    user-select: all;
  }

  .qr {
    width: 160px;
  }

  .detail-table {
    width: 100%;
    border-collapse: collapse;
  }

  .detail-table th,
  .detail-table td {
    text-align: left;
    padding: var(--fh-space-2) var(--fh-space-3);
    border-bottom: 1px solid var(--fh-rule);
    vertical-align: top;
  }

  .detail-table th {
    font-family: var(--fh-font-mono);
    font-size: var(--fh-text-mono-sm);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--fh-subtle);
    font-weight: 500;
  }

  .kind {
    font-size: var(--fh-text-mono-sm);
    color: var(--fh-subtle);
    margin-right: var(--fh-space-2);
  }

  .member-row td {
    color: var(--fh-ink-soft);
  }

  .member-name {
    padding-left: var(--fh-space-6) !important;
  }

  .row-hint {
    font-size: var(--fh-text-mono-sm);
    color: var(--fh-subtle);
    margin-left: var(--fh-space-2);
  }

  .fh-btn-text.danger {
    color: var(--fh-danger);
  }
</style>
