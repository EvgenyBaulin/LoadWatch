<script setup lang="ts">
// Пустое состояние: короткий заголовок, пояснение и действие.

withDefaults(defineProps<{ title: string; text?: string; compact?: boolean }>(), {
  text: undefined,
  compact: false,
})
</script>

<template>
  <div class="empty" :class="{ compact }">
    <p class="empty-title">{{ title }}</p>
    <p v-if="text" class="empty-text">{{ text }}</p>
    <div v-if="$slots.default" class="empty-actions">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: var(--space-1);
  padding: var(--space-5) var(--space-3);
  color: var(--text-muted);
  min-height: 140px;
}

.empty.compact {
  padding: var(--space-3);
  min-height: 0;
}

.empty-title {
  margin: 0;
  font-weight: 600;
  color: var(--text);
}

.empty-text {
  margin: 0;
  font-size: var(--fs-sm);
  max-width: 460px;
}

.empty-actions {
  margin-top: var(--space-2);
  display: flex;
  gap: var(--space-2);
}
</style>
