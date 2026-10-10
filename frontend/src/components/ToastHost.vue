<script setup lang="ts">
// Область всплывающих уведомлений (aria-live).

import { useI18n } from 'vue-i18n'
import { useToastStore } from '@/stores/toasts'
import Icon from './Icon.vue'

const toasts = useToastStore()
const { t } = useI18n()
</script>

<template>
  <div class="toasts" aria-live="polite" role="status">
    <div v-for="toast in toasts.items" :key="toast.id" class="toast callout" :class="`callout-${toast.kind}`">
      <span class="text">{{ toast.text }}</span>
      <button type="button" class="btn btn-ghost btn-icon btn-sm" :aria-label="t('common.close')" @click="toasts.dismiss(toast.id)">
        <Icon name="close" :size="12" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  right: var(--space-3);
  bottom: var(--space-3);
  z-index: 50;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-width: min(380px, calc(100vw - 24px));
}

.toast {
  background: var(--surface);
  box-shadow: var(--shadow);
  align-items: center;
}

.text {
  flex: 1;
}
</style>
