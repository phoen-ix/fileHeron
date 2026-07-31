<script setup lang="ts">
/* /approvals - the approver's work queue: shares awaiting this user's
 * approval. Click through to the share detail to review + approve/reject. */
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import Pager from '@/components/Pager.vue'
import { listPendingApprovals } from '@/api/shares'
import { useApiError } from '@/composables/useApiError'
import { useSiteDateFormat } from '@/composables/useSiteDateFormat'
import type { ShareListItem } from '@/types/api'
import { formatBytes } from '@/utils/bytes'

const { t } = useI18n()
const { describe } = useApiError()
const { formatDate } = useSiteDateFormat()

const items = ref<ShareListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(25)
const loading = ref(true)
const errorMsg = ref<string | null>(null)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    // Was a single hard-coded page of 100 with `total` discarded, so the 101st
    // pending share was invisible with nothing on screen to suggest more
    // existed - in the one queue whose entire purpose is that nothing sits in
    // it unnoticed (audit 2026-07-30, fe-correct-9).
    const { data } = await listPendingApprovals({
      page: page.value,
      page_size: pageSize.value,
    })
    items.value = data.items
    total.value = data.total
  } catch (err) {
    errorMsg.value = describe(err)
  } finally {
    loading.value = false
  }
}

watch(page, load)
onMounted(load)
</script>

<template>
  <div class="fh-page approvals" data-density="operator">
    <span class="fh-eyebrow">{{ t('approvals.eyebrow') }}</span>
    <h1 class="fh-display-md">{{ t('approvals.title') }}</h1>
    <p class="fh-field-help intro">{{ t('approvals.intro') }}</p>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="errorMsg" class="fh-notice" data-tone="error">{{ errorMsg }}</div>
    <p v-else-if="items.length === 0" class="fh-field-help empty">
      {{ t('approvals.empty') }}
    </p>

    <ul v-else class="queue">
      <li v-for="s in items" :key="s.id" class="queue-row">
        <div class="meta">
          <RouterLink
            :to="{ name: 'share-detail', params: { id: s.id } }"
            class="subject"
          >
            {{ s.effective_subject || t('approvals.no_subject') }}
          </RouterLink>
          <div class="sub fh-mono">
            <span v-if="s.sender">{{ t('approvals.from', { name: s.sender.display_name }) }} · </span>
            {{ t('approvals.file_count', { n: s.file_count }) }}
            · {{ formatBytes(s.total_size_bytes) }}
            · {{ formatDate(s.created_at) }}
          </div>
        </div>
        <RouterLink
          :to="{ name: 'share-detail', params: { id: s.id } }"
          class="fh-btn review-cta"
        >
          {{ t('approvals.review_cta') }}
        </RouterLink>
      </li>
    </ul>

    <Pager v-model:page="page" :total="total" :page-size="pageSize" />
  </div>
</template>

<style scoped>
.approvals {
  max-width: 860px;
}
.intro {
  margin: var(--fh-space-2) 0 var(--fh-space-4);
  max-width: 64ch;
}
.loading {
  color: var(--fh-subtle);
  padding: var(--fh-space-5) 0;
}
.empty {
  padding: var(--fh-space-5) 0;
}
.queue {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: var(--fh-border);
}
.queue-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fh-space-4);
  padding: var(--fh-space-3) 0;
  border-bottom: var(--fh-border);
}
.meta {
  min-width: 0;
}
.subject {
  font-size: var(--fh-text-body-md);
  color: var(--fh-ink);
  text-decoration: none;
}
.subject:hover {
  color: var(--fh-accent);
}
.sub {
  font-size: var(--fh-text-mono-sm);
  color: var(--fh-subtle);
  margin-top: 2px;
}
.review-cta {
  flex-shrink: 0;
}
@media (max-width: 640px) {
  .queue-row {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--fh-space-2);
  }
}
</style>
