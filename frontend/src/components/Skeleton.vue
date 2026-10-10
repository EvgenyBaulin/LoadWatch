<script setup lang="ts">
// Скелетон загрузки фиксированного размера (без сдвига макета).

withDefaults(defineProps<{ height?: number; width?: string; lines?: number }>(), {
  height: 14,
  width: '100%',
  lines: 1,
})
</script>

<template>
  <div class="skeleton-group" aria-hidden="true">
    <span
      v-for="i in lines"
      :key="i"
      class="skeleton"
      :style="{ height: `${height}px`, width: i === lines && lines > 1 ? '70%' : width }"
    />
  </div>
</template>

<style scoped>
.skeleton-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.skeleton {
  display: block;
  border-radius: var(--radius-sm);
  background: linear-gradient(90deg, var(--skeleton-from) 25%, var(--skeleton-to) 50%, var(--skeleton-from) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.4s ease-in-out infinite;
}

@keyframes shimmer {
  from {
    background-position: 200% 0;
  }

  to {
    background-position: -200% 0;
  }
}
</style>
