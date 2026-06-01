<script setup lang="ts">
import { computed, toRefs } from 'vue'
import { useI18n } from 'vue-i18n'

import { usePasswordStrength } from '@/composables/usePasswordStrength'

const props = defineProps<{ password: string }>()
const { password } = toRefs(props)
const strength = usePasswordStrength(password)
const { t } = useI18n()

const label = computed(() => t(`password_strength.${strength.value.label}`))
</script>

<template>
  <div v-if="password" class="ps">
    <div class="ps-bars">
      <span v-for="i in 4" :key="i" class="ps-bar" :data-on="i <= strength.score + 1" />
    </div>
    <span class="ps-label" :data-state="strength.label">{{ label }}</span>
  </div>
</template>

<style scoped>
.ps {
  display: flex;
  align-items: center;
  gap: var(--fh-space-2);
  margin-top: var(--fh-space-1);
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.ps-bars {
  display: inline-flex;
  gap: 3px;
}

.ps-bar {
  display: inline-block;
  width: 18px;
  height: 3px;
  background: var(--fh-hairline);
  transition: background var(--fh-duration-fast) var(--fh-easing);
}

.ps-bar[data-on='true'] {
  background: var(--fh-ink);
}

.ps-label[data-state='weak'] {
  color: var(--fh-danger);
}
.ps-label[data-state='fair'] {
  color: var(--fh-warning);
}
.ps-label[data-state='good'] {
  color: var(--fh-subtle);
}
.ps-label[data-state='strong'] {
  color: var(--fh-success);
}
</style>
