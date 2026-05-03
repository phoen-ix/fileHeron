<script setup lang="ts">
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
</script>

<template>
  <div class="home">
    <div class="welcome-card fh-rise" data-stagger="2">
      <div class="welcome-line">
        <span class="fh-kv-label">/ user</span>
        <span class="fh-kv-value">{{ auth.user?.display_name }}</span>
      </div>
      <div class="welcome-line">
        <span class="fh-kv-label">/ role</span>
        <span class="fh-kv-value">{{ auth.user?.role }}</span>
      </div>
      <div class="welcome-actions">
        <RouterLink :to="{ name: 'share-create' }" class="fh-btn">
          {{ $t('home.go_share') }} <span aria-hidden="true">→</span>
        </RouterLink>
        <RouterLink to="/account" class="fh-btn-ghost fh-btn">
          {{ $t('home.go_account') }}
        </RouterLink>
      </div>
    </div>
  </div>
</template>

<style scoped>
.home {
  display: flex;
  justify-content: center;
  padding-top: var(--fh-space-6);
}

.welcome-card {
  width: 100%;
  max-width: 480px;
  padding: var(--fh-space-5);
  border-top: 1px solid var(--fh-hairline);
  display: flex;
  flex-direction: column;
  gap: var(--fh-space-2);
}

.welcome-line {
  display: flex;
  align-items: baseline;
  gap: var(--fh-space-3);
  font-size: var(--fh-text-body-md);
}

.welcome-line .fh-kv-value {
  color: var(--fh-ink);
}

.welcome-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--fh-space-3);
  margin-top: var(--fh-space-3);
}
</style>
