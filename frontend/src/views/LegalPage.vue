<template>
  <div class="fh-prose legal-page">
    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>

    <template v-else-if="available">
      <span class="fh-eyebrow fh-rise" data-stagger="1">{{ t('legal.eyebrow') }}</span>
      <h1 class="fh-display fh-rise" data-stagger="2">{{ title }}</h1>
      <hr class="fh-rule" />
      <!-- Server-sanitised (markdown-it raw HTML off + nh3 allowlist), so the
           raw-HTML sink is the intended, reviewed render path. -->
      <!-- eslint-disable-next-line vue/no-v-html -->
      <div class="legal-content fh-rise" data-stagger="3" v-html="html"></div>
    </template>

    <div v-else class="error-state">
      <span class="fh-eyebrow">{{ t('legal.eyebrow') }}</span>
      <h1 class="fh-display-md">{{ title }}</h1>
      <p class="fh-field-help">{{ t('legal.not_available') }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { getLegal, type LegalContentResponse } from '@/api/legal'

const route = useRoute()
const { t, locale } = useI18n()

const kind = computed<'imprint' | 'privacy'>(() =>
  route.name === 'privacy' ? 'privacy' : 'imprint',
)
const title = computed(() => t(`legal.${kind.value}`))

const loading = ref(true)
const data = ref<LegalContentResponse | null>(null)

const html = computed(() => {
  if (!data.value) return ''
  const de = data.value.html_de
  const en = data.value.html_en
  // Show the viewer's language; fall back to the other when empty.
  return locale.value === 'de' ? de || en : en || de
})
const available = computed(() => !!data.value?.enabled && !!html.value)

async function load() {
  loading.value = true
  data.value = null
  try {
    const { data: d } = await getLegal(kind.value)
    data.value = d
  } catch {
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(kind, load)
onMounted(load)
</script>

<style scoped>
.legal-page {
  max-width: 720px;
}
.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-3) 0;
}
.legal-content :deep(h1),
.legal-content :deep(h2),
.legal-content :deep(h3) {
  font-family: var(--fh-font-display);
  font-weight: 400;
  margin: var(--fh-space-4) 0 var(--fh-space-2);
}
.legal-content :deep(p),
.legal-content :deep(ul),
.legal-content :deep(ol) {
  margin: 0 0 var(--fh-space-2);
  line-height: 1.6;
}
.legal-content :deep(a) {
  color: var(--fh-accent);
}
</style>
