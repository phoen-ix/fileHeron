<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'

import {
  deleteInboxMessage,
  downloadInboxAttachment,
  getInboxMessage,
  updateInboxStatus,
} from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import { useUiStore } from '@/stores/ui'
import { downloadBlob } from '@/utils/downloadBlob'
import { formatBytes } from '@/utils/bytes'
import type { InboxDetail } from '@/types/api'

const { t } = useI18n()
const { describe } = useApiError()
const { formatDate } = useSiteDateFormat()
const ui = useUiStore()
const route = useRoute()
const router = useRouter()

const id = Number(route.params.id)
const loading = ref(true)
const errorMsg = ref<string | null>(null)
const msg = ref<InboxDetail | null>(null)
const view = ref<'html' | 'text'>('html')

const hasHtml = computed(() => !!msg.value?.body_html)

async function load() {
  loading.value = true
  try {
    const { data } = await getInboxMessage(id)
    msg.value = data
    view.value = data.body_html ? 'html' : 'text'
    // Opening a new message clears it from the unread count.
    if (data.status === 'new') {
      const { data: updated } = await updateInboxStatus(id, { status: 'read' })
      msg.value = updated
    }
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

async function setStatus(status: 'read' | 'archived') {
  if (!msg.value) return
  try {
    const { data } = await updateInboxStatus(id, { status })
    msg.value = data
    ui.pushToast(t('admin_inbox.status_updated'), 'success')
  } catch (err) {
    ui.pushToast(describe(err), 'warn')
  }
}

async function onDelete() {
  if (!(await ui.confirm({ message: t('admin_inbox.delete_confirm'), danger: true }))) return
  try {
    await deleteInboxMessage(id)
    ui.pushToast(t('admin_inbox.deleted'), 'success')
    router.push({ name: 'admin-inbox' })
  } catch (err) {
    ui.pushToast(describe(err), 'warn')
  }
}

async function download(attId: number, filename: string) {
  try {
    const { data } = await downloadInboxAttachment(id, attId)
    downloadBlob(data as Blob, filename)
  } catch (err) {
    ui.pushToast(describe(err), 'warn')
  }
}

onMounted(load)
</script>

<template>
  <div class="msg-page" data-density="operator">
    <RouterLink :to="{ name: 'admin-inbox' }" class="fh-btn-text back">← {{ t('admin_inbox.back') }}</RouterLink>

    <div
v-if="errorMsg" class="fh-notice" role="alert"
        data-tone="danger">{{ errorMsg }}</div>
    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <article v-else-if="msg" class="msg">
      <header class="head">
        <span class="badge" :data-tone="msg.classification">{{ t(`admin_inbox.tag_${msg.classification}`) }}</span>
        <h1 class="subject">{{ msg.subject }}</h1>
        <dl class="meta">
          <div><dt>{{ t('admin_inbox.from') }}</dt><dd>{{ msg.sender_name ? `${msg.sender_name} <${msg.sender_email}>` : msg.sender_email }}</dd></div>
          <div v-if="msg.to_addr"><dt>{{ t('admin_inbox.to') }}</dt><dd>{{ msg.to_addr }}</dd></div>
          <div><dt>{{ t('admin_inbox.received') }}</dt><dd>{{ formatDate(msg.received_at || msg.created_at) }}</dd></div>
        </dl>
      </header>

      <div class="toolbar">
        <button v-if="hasHtml" type="button" class="seg" :class="{ on: view === 'html' }" @click="view = 'html'">{{ t('admin_inbox.view_html') }}</button>
        <button v-if="hasHtml" type="button" class="seg" :class="{ on: view === 'text' }" @click="view = 'text'">{{ t('admin_inbox.view_text') }}</button>
        <span class="spacer" />
        <button type="button" class="fh-btn-text" @click="setStatus('archived')">{{ t('admin_inbox.archive') }}</button>
        <button type="button" class="fh-btn-text danger" @click="onDelete">{{ t('common.delete') }}</button>
      </div>

      <!-- Untrusted inbound HTML: sanitised at ingest AND sandboxed here. -->
      <iframe
        v-if="hasHtml && view === 'html'"
        class="body-frame"
        sandbox=""
        :srcdoc="msg.body_html || ''"
        :title="t('admin_inbox.body')"
      />
      <pre v-else class="body-text">{{ msg.body_text || t('admin_inbox.no_body') }}</pre>

      <section v-if="msg.attachments.length" class="attachments">
        <h2 class="form-h2">{{ t('admin_inbox.attachments') }}</h2>
        <ul>
          <li v-for="a in msg.attachments" :key="a.id" class="att">
            <span class="att-name">{{ a.filename }}</span>
            <span class="fh-mono att-meta">{{ formatBytes(a.size_bytes) }} · {{ a.content_type || '-' }}</span>
            <button
              v-if="a.av_state === 'clean'"
              type="button"
              class="fh-btn-text"
              @click="download(a.id, a.filename)"
            >
              {{ t('admin_inbox.download') }}
            </button>
            <span v-else class="att-state" :data-tone="a.av_state">{{ t(`admin_inbox.av_${a.av_state}`) }}</span>
          </li>
        </ul>
      </section>
    </article>
  </div>
</template>

<style scoped>
.back {
  display: inline-block;
  margin-bottom: var(--fh-space-3);
}
.badge {
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  padding: 0.1rem 0.4rem;
  border: 1px solid var(--fh-hairline);
  border-radius: var(--fh-radius-sm);
}
.badge[data-tone='bounce'] {
  color: var(--fh-danger);
  border-color: var(--fh-danger);
}
.badge[data-tone='auto_reply'] {
  color: var(--fh-warning);
  border-color: var(--fh-warning);
}
.subject {
  font-family: var(--fh-font-display);
  font-weight: normal;
  font-size: var(--fh-text-display-md);
  margin: var(--fh-space-2) 0;
}
.meta {
  display: grid;
  gap: 0.2rem;
  margin: 0 0 var(--fh-space-3);
}
.meta div {
  display: flex;
  gap: var(--fh-space-2);
}
.meta dt {
  color: var(--fh-ink-soft);
  min-width: 5rem;
}
.meta dd {
  margin: 0;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: var(--fh-space-2);
  border-top: 1px solid var(--fh-hairline);
  border-bottom: 1px solid var(--fh-hairline);
  padding: var(--fh-space-2) 0;
  margin-bottom: var(--fh-space-3);
}
.toolbar .spacer {
  flex: 1;
}
.seg {
  border: 1px solid var(--fh-hairline);
  background: var(--fh-paper);
  padding: 0.2rem 0.6rem;
  cursor: pointer;
}
.seg.on {
  background: var(--fh-accent-soft);
  color: var(--fh-accent);
}
.body-frame {
  width: 100%;
  height: 32rem;
  border: 1px solid var(--fh-hairline);
  border-radius: var(--fh-radius-md);
  background: #fff;
}
.body-text {
  white-space: pre-wrap;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  background: var(--fh-paper-sunk);
  padding: var(--fh-space-3);
  border-radius: var(--fh-radius-md);
}
.att {
  display: flex;
  align-items: center;
  gap: var(--fh-space-3);
  padding: 0.3rem 0;
}
.att-meta {
  color: var(--fh-ink-soft);
  font-size: var(--fh-text-mono-sm);
}
.att-state[data-tone='infected'] {
  color: var(--fh-danger);
}
</style>
