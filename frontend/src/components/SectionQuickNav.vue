<script setup lang="ts">
import { useI18n } from 'vue-i18n'

export interface QuickNavSection {
  id: string
  labelKey: string
}

defineProps<{
  sections: QuickNavSection[]
  active: string
  ariaLabel: string
}>()

defineEmits<{
  jump: [id: string]
}>()

const { t } = useI18n()
</script>

<template>
  <nav class="quicknav" :aria-label="ariaLabel">
    <ul>
      <li v-for="s in sections" :key="s.id">
        <button
          type="button"
          class="quicknav-item"
          :class="{ 'is-active': s.id === active }"
          :aria-current="s.id === active ? 'true' : undefined"
          @click="$emit('jump', s.id)"
        >
          <span class="quicknav-rule" aria-hidden="true"></span>
          <span class="quicknav-label">{{ t(s.labelKey) }}</span>
        </button>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.quicknav ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.quicknav-item {
  display: flex;
  align-items: center;
  gap: var(--fh-space-2);
  width: 100%;
  background: none;
  border: 0;
  padding: var(--fh-space-2) 0;
  cursor: pointer;
  text-align: left;
  font-family: var(--fh-font-mono);
  font-size: var(--fh-text-mono-sm);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--fh-subtle);
  transition: color var(--fh-duration-fast) var(--fh-easing);
}

.quicknav-item:hover {
  color: var(--fh-ink);
}

.quicknav-item:focus-visible {
  outline: 2px solid var(--fh-accent);
  outline-offset: 2px;
}

.quicknav-rule {
  display: inline-block;
  width: 1px;
  height: 0.875rem;
  background: var(--fh-hairline);
  transition:
    width var(--fh-duration-fast) var(--fh-easing),
    background var(--fh-duration-fast) var(--fh-easing);
}

.quicknav-item.is-active {
  color: var(--fh-accent);
}

.quicknav-item.is-active .quicknav-rule {
  width: 2px;
  background: var(--fh-accent);
}

.quicknav-label {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
