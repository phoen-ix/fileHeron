<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { useI18n } from 'vue-i18n'

import {
  getEmailTemplate,
  getEmailTemplates,
  previewEmailTemplate,
  resetEmailTemplate,
  testSendEmailTemplate,
  updateEmailTemplate,
} from '@/api/admin'
import { useApiError } from '@/composables/useApiError'
import { useUiStore } from '@/stores/ui'
import type {
  EmailPlaceholderMeta,
  EmailTemplatesListResponse,
  PreviewEmailTemplateResponse,
} from '@/types/api'

// Lazy chunk: the ProseMirror RichTextEditor only loads when this page is opened.
const RichTextEditor = defineAsyncComponent(() => import('@/components/RichTextEditor.vue'))

const { t, te } = useI18n()
const { describe } = useApiError()
const ui = useUiStore()

const loading = ref(true)
const detailLoading = ref(false)
const saving = ref(false)
const previewing = ref(false)
const testing = ref(false)
const errorMsg = ref<string | null>(null)

const summary = ref<EmailTemplatesListResponse | null>(null)
const activeSlug = ref<string | null>(null)
const activeLocale = ref<string | null>(null)

const subject = ref('')
const body = ref('')
const baseline = ref<{ subject: string; body: string }>({ subject: '', body: '' })
const customized = ref(false)

const preview = ref<PreviewEmailTemplateResponse | null>(null)
const previewMode = ref<'html' | 'text'>('html')
const placeholderOpen = ref(false)
const editorRef = ref<{ insertText: (t: string) => void } | null>(null)

const dirty = computed(
  () => subject.value !== baseline.value.subject || body.value !== baseline.value.body,
)

const activePlaceholders = computed<EmailPlaceholderMeta[]>(() =>
  activeSlug.value ? (summary.value?.placeholders[activeSlug.value] ?? []) : [],
)

const editorPlaceholders = computed(() =>
  activePlaceholders.value.map((p) => ({ token: p.token, label: p.label })),
)

const groupedItems = computed(() => {
  if (!summary.value) return []
  return summary.value.groups.map((g) => ({
    key: g,
    items: summary.value!.items.filter((i) => i.group === g),
  }))
})

function templateName(slug: string): string {
  const key = `admin_email_templates.template.${slug}`
  return te(key) ? t(key) : slug
}

function groupName(key: string): string {
  const k = `admin_email_templates.group.${key}`
  return te(k) ? t(k) : key
}

function localeLabel(code: string): string {
  const k = `admin_email_templates.locale.${code}`
  return te(k) ? t(k) : code
}

function isCustomizedFor(slug: string, locale: string | null): boolean {
  if (!locale) return false
  return summary.value?.items.find((i) => i.slug === slug)?.has_override[locale] ?? false
}

async function confirmDiscardIfDirty(): Promise<boolean> {
  if (!dirty.value) return true
  return ui.confirm({ message: t('admin_email_templates.discard_confirm') })
}

async function loadDetail() {
  if (!activeSlug.value || !activeLocale.value) return
  detailLoading.value = true
  errorMsg.value = null
  preview.value = null
  try {
    const { data } = await getEmailTemplate(activeSlug.value, activeLocale.value)
    subject.value = data.subject
    body.value = data.has_override ? data.body_html : data.default_body
    baseline.value = { subject: subject.value, body: body.value }
    customized.value = data.has_override
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    detailLoading.value = false
  }
}

async function selectTemplate(slug: string) {
  if (slug === activeSlug.value) return
  if (!(await confirmDiscardIfDirty())) return
  activeSlug.value = slug
  await loadDetail()
}

async function selectLocale(code: string) {
  if (code === activeLocale.value) return
  if (!(await confirmDiscardIfDirty())) return
  activeLocale.value = code
  await loadDetail()
}

function markOverride(slug: string, locale: string, has: boolean) {
  const item = summary.value?.items.find((i) => i.slug === slug)
  if (item) item.has_override[locale] = has
}

async function onSave() {
  if (!activeSlug.value || !activeLocale.value) return
  saving.value = true
  errorMsg.value = null
  try {
    const { data } = await updateEmailTemplate(activeSlug.value, activeLocale.value, {
      subject: subject.value.trim() || null,
      body_html: body.value,
    })
    subject.value = data.subject
    baseline.value = { subject: subject.value, body: body.value }
    customized.value = data.has_override
    markOverride(activeSlug.value, activeLocale.value, data.has_override)
    ui.pushToast(t('admin_email_templates.saved_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

async function onReset() {
  if (!activeSlug.value || !activeLocale.value) return
  if (!(await ui.confirm({ message: t('admin_email_templates.reset_confirm'), danger: true }))) return
  saving.value = true
  errorMsg.value = null
  try {
    const { data } = await resetEmailTemplate(activeSlug.value, activeLocale.value)
    subject.value = data.subject
    body.value = data.default_body
    baseline.value = { subject: subject.value, body: body.value }
    customized.value = false
    markOverride(activeSlug.value, activeLocale.value, false)
    ui.pushToast(t('admin_email_templates.reset_toast'), 'success')
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    saving.value = false
  }
}

async function onPreview() {
  if (!activeSlug.value || !activeLocale.value) return
  previewing.value = true
  errorMsg.value = null
  try {
    const { data } = await previewEmailTemplate(activeSlug.value, activeLocale.value, {
      subject: subject.value.trim() || null,
      body_html: body.value,
    })
    preview.value = data
    previewMode.value = 'html'
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    previewing.value = false
  }
}

async function onTestSend() {
  if (!activeSlug.value || !activeLocale.value) return
  testing.value = true
  try {
    const { data } = await testSendEmailTemplate(activeSlug.value, activeLocale.value, {
      subject: subject.value.trim() || null,
      body_html: body.value,
    })
    if (data.ok) {
      ui.pushToast(t('admin_email_templates.test_ok_toast', { to: data.sent_to ?? '' }), 'success')
    } else {
      ui.pushToast(data.hint || data.error_message || t('admin_email_templates.test_failed_toast'), 'warn')
    }
  } catch (err) {
    ui.pushToast(describe(err), 'warn')
  } finally {
    testing.value = false
  }
}

function insertPlaceholder(token: string) {
  editorRef.value?.insertText(token)
}

onMounted(async () => {
  try {
    const { data } = await getEmailTemplates()
    summary.value = data
    activeLocale.value = data.locales[0]?.code ?? null
    activeSlug.value = data.items[0]?.slug ?? null
    await loadDetail()
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
})

onBeforeRouteLeave(async () => {
  if (!dirty.value) return true
  return ui.confirm({ message: t('admin_email_templates.leave_confirm') })
})
</script>

<template>
  <div class="tpl-page" data-density="operator">
    <span class="fh-eyebrow">
      {{ t('admin_settings.eyebrow') }} / {{ t('admin_email_templates.title') }}
    </span>
    <p class="fh-field-help intro">{{ t('admin_email_templates.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <div v-else class="tpl-layout">
      <!-- Template picker -->
      <nav class="tpl-picker" :aria-label="t('admin_email_templates.list_heading')">
        <div v-for="grp in groupedItems" :key="grp.key" class="picker-group">
          <h2 class="form-h2">{{ groupName(grp.key) }}</h2>
          <ul>
            <li v-for="it in grp.items" :key="it.slug">
              <button
                type="button"
                class="picker-item"
                :class="{ active: it.slug === activeSlug }"
                @click="selectTemplate(it.slug)"
              >
                <span>{{ templateName(it.slug) }}</span>
                <span
                  v-if="isCustomizedFor(it.slug, activeLocale)"
                  class="dot"
                  :title="t('admin_email_templates.badge_customized')"
                />
              </button>
            </li>
          </ul>
        </div>
      </nav>

      <!-- Editor pane -->
      <section v-if="activeSlug && activeLocale" class="tpl-editor">
        <header class="editor-head">
          <h2 class="editor-title">{{ templateName(activeSlug) }}</h2>
          <span class="fh-pill" :data-state="customized ? 'active' : undefined">
            {{ customized ? t('admin_email_templates.badge_customized') : t('admin_email_templates.badge_default') }}
          </span>
        </header>

        <!-- Locale tabs -->
        <div class="locale-tabs" role="tablist">
          <button
            v-for="loc in summary?.locales ?? []"
            :key="loc.code"
            type="button"
            role="tab"
            class="locale-tab"
            :class="{ active: loc.code === activeLocale }"
            :aria-pressed="loc.code === activeLocale"
            @click="selectLocale(loc.code)"
          >
            {{ localeLabel(loc.code) }}
          </button>
        </div>

        <div v-if="errorMsg" class="fh-notice" data-tone="danger">{{ errorMsg }}</div>

        <div v-if="detailLoading" class="loading">{{ t('common.loading') }}</div>

        <template v-else>
          <label class="fh-field">
            <span class="fh-field-label">{{ t('admin_email_templates.subject_label') }}</span>
            <input v-model="subject" type="text" class="fh-field-input" :disabled="saving" />
          </label>

          <div class="fh-field">
            <span class="fh-field-label">{{ t('admin_email_templates.body_label') }}</span>
            <RichTextEditor
              ref="editorRef"
              v-model="body"
              :placeholders="editorPlaceholders"
              :disabled="saving"
              :aria-label="t('admin_email_templates.body_label')"
            />
          </div>

          <!-- Placeholder reference (collapsible) -->
          <details class="placeholders" :open="placeholderOpen" @toggle="placeholderOpen = ($event.target as HTMLDetailsElement).open">
            <summary>{{ t('admin_email_templates.placeholders_heading') }}</summary>
            <p class="fh-field-help">{{ t('admin_email_templates.placeholders_help') }}</p>
            <ul class="placeholder-list">
              <li v-for="p in activePlaceholders" :key="p.token">
                <button type="button" class="fh-mono ph-token" @click="insertPlaceholder(p.token)">
                  {{ p.token }}
                </button>
                <span class="ph-desc">
                  {{ p.description }}
                  <span v-if="p.required" class="ph-required">{{ t('admin_email_templates.required_tag') }}</span>
                </span>
              </li>
            </ul>
          </details>

          <!-- Actions -->
          <div class="actions">
            <button type="button" class="fh-btn" :disabled="saving || !dirty" @click="onSave">
              {{ t('common.save') }}
            </button>
            <button type="button" class="fh-btn-text" :disabled="previewing" @click="onPreview">
              {{ t('admin_email_templates.preview') }}
            </button>
            <button type="button" class="fh-btn-text" :disabled="testing" @click="onTestSend">
              {{ t('admin_email_templates.test_send') }}
            </button>
            <button
              type="button"
              class="fh-btn-text danger"
              :disabled="saving || !customized"
              @click="onReset"
            >
              {{ t('admin_email_templates.reset') }}
            </button>
          </div>

          <!-- Preview -->
          <section v-if="preview" class="preview">
            <div class="preview-head">
              <h2 class="form-h2">{{ t('admin_email_templates.preview_title') }}</h2>
              <div class="preview-toggle">
                <button type="button" :class="{ active: previewMode === 'html' }" @click="previewMode = 'html'">
                  {{ t('admin_email_templates.preview_html_tab') }}
                </button>
                <button type="button" :class="{ active: previewMode === 'text' }" @click="previewMode = 'text'">
                  {{ t('admin_email_templates.preview_text_tab') }}
                </button>
              </div>
            </div>
            <p class="preview-subject">
              <span class="fh-field-label">{{ t('admin_email_templates.preview_subject_label') }}</span>
              {{ preview.subject }}
            </p>
            <iframe
              v-if="previewMode === 'html'"
              class="preview-frame"
              sandbox=""
              :srcdoc="preview.html"
              :title="t('admin_email_templates.preview_title')"
            />
            <pre v-else class="preview-text">{{ preview.text }}</pre>
          </section>
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.tpl-page {
  max-width: var(--fh-max-width-page);
}
.intro {
  margin: var(--fh-space-2) 0 var(--fh-space-4);
}
.tpl-layout {
  display: grid;
  grid-template-columns: 16rem 1fr;
  gap: var(--fh-space-5);
  align-items: start;
}
@media (max-width: 720px) {
  .tpl-layout {
    grid-template-columns: 1fr;
  }
}
.tpl-picker {
  border-right: 1px solid var(--fh-hairline);
  padding-right: var(--fh-space-3);
}
.picker-group + .picker-group {
  margin-top: var(--fh-space-4);
}
.tpl-picker ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
.picker-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fh-space-2);
  width: 100%;
  text-align: left;
  padding: 0.4rem 0.5rem;
  border: none;
  border-radius: var(--fh-radius-sm);
  background: none;
  color: var(--fh-ink);
  font-family: var(--fh-font-body);
  font-size: var(--fh-text-body-sm);
  cursor: pointer;
}
.picker-item:hover {
  background: var(--fh-paper-sunk);
}
.picker-item.active {
  background: var(--fh-accent-soft);
  color: var(--fh-accent);
}
.dot {
  width: 0.45rem;
  height: 0.45rem;
  border-radius: 50%;
  background: var(--fh-accent);
  flex: none;
}
.editor-head {
  display: flex;
  align-items: center;
  gap: var(--fh-space-3);
  margin-bottom: var(--fh-space-3);
}
.editor-title {
  font-family: var(--fh-font-display);
  font-size: var(--fh-text-display-md);
  font-weight: normal;
  margin: 0;
}
.locale-tabs {
  display: flex;
  gap: var(--fh-space-1);
  margin-bottom: var(--fh-space-4);
  border-bottom: 1px solid var(--fh-hairline);
}
.locale-tab {
  padding: 0.4rem 0.9rem;
  border: none;
  border-bottom: 2px solid transparent;
  background: none;
  color: var(--fh-ink-soft);
  font-family: var(--fh-font-body);
  cursor: pointer;
}
.locale-tab.active {
  color: var(--fh-ink);
  border-bottom-color: var(--fh-accent);
}
.fh-field {
  display: block;
  margin-bottom: var(--fh-space-4);
}
.placeholders {
  margin-bottom: var(--fh-space-4);
  border: 1px solid var(--fh-hairline);
  border-radius: var(--fh-radius-md);
  padding: var(--fh-space-2) var(--fh-space-3);
}
.placeholders summary {
  cursor: pointer;
  font-weight: 500;
}
.placeholder-list {
  list-style: none;
  margin: var(--fh-space-2) 0 0;
  padding: 0;
  display: grid;
  gap: var(--fh-space-2);
}
.placeholder-list li {
  display: flex;
  gap: var(--fh-space-2);
  align-items: baseline;
}
.ph-token {
  border: 1px solid var(--fh-hairline);
  border-radius: var(--fh-radius-sm);
  background: var(--fh-paper-sunk);
  color: var(--fh-accent);
  padding: 0.1rem 0.4rem;
  cursor: pointer;
  white-space: nowrap;
}
.ph-desc {
  color: var(--fh-ink-soft);
  font-size: var(--fh-text-body-sm);
}
.ph-required {
  color: var(--fh-danger);
  margin-left: 0.4rem;
}
.actions {
  display: flex;
  align-items: center;
  gap: var(--fh-space-3);
  margin-bottom: var(--fh-space-4);
}
.actions .danger {
  margin-left: auto;
}
.preview {
  border-top: 1px solid var(--fh-hairline);
  padding-top: var(--fh-space-4);
}
.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.preview-toggle button {
  border: 1px solid var(--fh-hairline);
  background: var(--fh-paper);
  padding: 0.2rem 0.6rem;
  cursor: pointer;
}
.preview-toggle button.active {
  background: var(--fh-accent-soft);
  color: var(--fh-accent);
}
.preview-subject {
  margin: var(--fh-space-2) 0;
}
.preview-frame {
  width: 100%;
  height: 32rem;
  border: 1px solid var(--fh-hairline);
  border-radius: var(--fh-radius-md);
  background: #fff;
}
.preview-text {
  white-space: pre-wrap;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  background: var(--fh-paper-sunk);
  padding: var(--fh-space-3);
  border-radius: var(--fh-radius-md);
  overflow-x: auto;
}
</style>
