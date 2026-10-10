<script setup lang="ts">
// Stat-панель: крупное значение, цвет по порогу, подпись снизу.

import Skeleton from './Skeleton.vue'

export type StatLevel = 'ok' | 'warn' | 'bad' | 'neutral'

withDefaults(
  defineProps<{
    title: string
    value: string
    level?: StatLevel
    hint?: string
    loading?: boolean
    levelText?: string
  }>(),
  { level: 'neutral', hint: undefined, loading: false, levelText: undefined },
)
</script>

<template>
  <section class="stat" :class="`level-${level}`" :aria-label="title">
    <h3 class="stat-title">{{ title }}</h3>
    <Skeleton v-if="loading" :height="30" :width="'60%'" />
    <p v-else class="stat-value num">
      {{ value }}
      <span v-if="levelText" class="sr-only">{{ levelText }}</span>
    </p>
    <p class="stat-hint">
      <slot name="hint">{{ hint }}</slot>
    </p>
  </section>
</template>

<style scoped>
.stat {
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--border-strong);
  border-radius: var(--radius);
  padding: var(--space-2) var(--space-3);
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  min-height: 96px;
}

.stat-title {
  font-size: var(--fs-xs);
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.stat-value {
  margin: 0;
  font-size: var(--fs-stat);
  font-weight: 600;
  line-height: 1.15;
  color: var(--threshold-neutral);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.stat-hint {
  margin: 0;
  font-size: var(--fs-xs);
  color: var(--text-muted);
  min-height: 1.4em;
}

.level-ok {
  border-top-color: var(--threshold-ok);
}

.level-ok .stat-value {
  color: var(--threshold-ok);
}

.level-warn {
  border-top-color: var(--threshold-warn);
}

.level-warn .stat-value {
  color: var(--threshold-warn);
}

.level-bad {
  border-top-color: var(--threshold-bad);
}

.level-bad .stat-value {
  color: var(--threshold-bad);
}
</style>
