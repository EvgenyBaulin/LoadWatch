<script setup lang="ts">
// Панель дашборда: заголовок, необязательные действия справа, содержимое.

withDefaults(defineProps<{ title?: string; description?: string; flush?: boolean }>(), {
  title: undefined,
  description: undefined,
  flush: false,
})
</script>

<template>
  <section class="panel" :aria-label="title">
    <header v-if="title || $slots.actions" class="panel-header">
      <div class="panel-titles">
        <h2 v-if="title" class="panel-title">{{ title }}</h2>
        <p v-if="description" class="panel-desc">{{ description }}</p>
      </div>
      <div v-if="$slots.actions" class="panel-actions">
        <slot name="actions" />
      </div>
    </header>
    <div class="panel-body" :class="{ flush }">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: 6px var(--space-3);
  border-bottom: 1px solid var(--border);
  min-height: 34px;
  flex-wrap: wrap;
}

.panel-title {
  font-size: var(--fs-sm);
  font-weight: 600;
}

.panel-desc {
  margin: 0;
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.panel-actions {
  display: flex;
  gap: var(--space-1);
  align-items: center;
  flex-wrap: wrap;
}

.panel-body {
  padding: var(--space-3);
  flex: 1;
  min-width: 0;
}

.panel-body.flush {
  padding: 0;
}
</style>
