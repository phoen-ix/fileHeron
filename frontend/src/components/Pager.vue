<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ page: number; total: number; pageSize: number }>()
const emit = defineEmits<{ 'update:page': [n: number] }>()
const { t } = useI18n()

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
</script>

<template>
  <div v-if="totalPages > 1" class="pager">
    <button
      v-if="page > 1"
      type="button"
      class="fh-btn-text"
      @click="emit('update:page', page - 1)"
    >
      ← {{ t('admin_users.prev') }}
    </button>
    <span class="fh-mono page-info">
      {{ t('admin_users.page_of', { page, total: totalPages }) }}
    </span>
    <button
      v-if="page < totalPages"
      type="button"
      class="fh-btn-text"
      @click="emit('update:page', page + 1)"
    >
      {{ t('admin_users.next') }} →
    </button>
  </div>
</template>

<style scoped>
.pager {
  display: flex;
  gap: var(--fh-space-3);
  align-items: baseline;
  justify-content: center;
  margin-top: var(--fh-space-4);
}

.page-info {
  color: var(--fh-subtle);
  font-size: var(--fh-text-mono-sm);
}
</style>
