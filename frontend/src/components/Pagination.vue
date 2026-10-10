<script setup lang="ts">
// Постраничная навигация по offset/limit.

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ total: number; limit: number; offset: number }>()
const emit = defineEmits<{ change: [offset: number] }>()
const { t } = useI18n()

const page = computed(() => Math.floor(props.offset / props.limit) + 1)
const pages = computed(() => Math.max(1, Math.ceil(props.total / props.limit)))
</script>

<template>
  <nav class="pager" :aria-label="t('table.pagination')">
    <span class="muted small">{{ t('table.pageOf', { page, pages }) }} · {{ t('table.total', { n: total }, total) }}</span>
    <div class="row">
      <button type="button" class="btn btn-sm" :disabled="page <= 1" @click="emit('change', offset - limit)">
        {{ t('table.prev') }}
      </button>
      <button type="button" class="btn btn-sm" :disabled="page >= pages" @click="emit('change', offset + limit)">
        {{ t('table.next') }}
      </button>
    </div>
  </nav>
</template>

<style scoped>
.pager {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  flex-wrap: wrap;
}
</style>
