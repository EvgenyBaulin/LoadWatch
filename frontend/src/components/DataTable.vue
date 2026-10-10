<script setup lang="ts" generic="T extends object">
// Компактная таблица: колонки с сортировкой (aria-sort), слоты для ячеек, скелетон и пустое состояние.

import { useI18n } from 'vue-i18n'
import Skeleton from './Skeleton.vue'

export interface Column {
  key: string
  label: string
  sortable?: boolean
  numeric?: boolean
  width?: string
}

export interface SortState {
  key: string
  dir: 'asc' | 'desc'
}

withDefaults(
  defineProps<{
    columns: Column[]
    rows: T[]
    rowKey: (row: T) => string
    loading?: boolean
    caption: string
    skeletonRows?: number
  }>(),
  { loading: false, skeletonRows: 5 },
)

const sort = defineModel<SortState | null>('sort', { default: null })
const { t } = useI18n()

function toggle(key: string): void {
  if (sort.value?.key === key) {
    sort.value = { key, dir: sort.value.dir === 'asc' ? 'desc' : 'asc' }
  } else {
    sort.value = { key, dir: 'desc' }
  }
}

function ariaSort(key: string): 'ascending' | 'descending' | 'none' {
  if (sort.value?.key !== key) return 'none'
  return sort.value.dir === 'asc' ? 'ascending' : 'descending'
}

function cell(row: T, key: string): unknown {
  return (row as Record<string, unknown>)[key]
}
</script>

<template>
  <div class="table-wrap">
    <table class="table">
      <caption class="sr-only">{{ caption }}</caption>
      <thead>
        <tr>
          <th
            v-for="col in columns"
            :key="col.key"
            scope="col"
            :class="{ num: col.numeric }"
            :style="col.width ? { width: col.width } : undefined"
            :aria-sort="col.sortable ? ariaSort(col.key) : undefined"
          >
            <button
              v-if="col.sortable"
              type="button"
              class="sort-btn"
              :title="t('table.sortBy', { column: col.label })"
              @click="toggle(col.key)"
            >
              {{ col.label }}
              <span class="sort-mark" aria-hidden="true">{{
                sort?.key === col.key ? (sort.dir === 'asc' ? '▲' : '▼') : '↕'
              }}</span>
            </button>
            <template v-else>{{ col.label }}</template>
          </th>
        </tr>
      </thead>
      <tbody v-if="loading && !rows.length">
        <tr v-for="i in skeletonRows" :key="i">
          <td v-for="col in columns" :key="col.key"><Skeleton :height="12" /></td>
        </tr>
      </tbody>
      <tbody v-else>
        <tr v-for="row in rows" :key="rowKey(row)">
          <td v-for="col in columns" :key="col.key" :class="{ num: col.numeric }">
            <slot :name="`cell-${col.key}`" :row="row" :value="cell(row, col.key)">
              {{ cell(row, col.key) }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
    <slot v-if="!loading && !rows.length" name="empty" />
  </div>
</template>

<style scoped>
.sort-btn {
  all: unset;
  cursor: pointer;
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.sort-btn:focus-visible {
  outline: 2px solid var(--focus-ring);
}

.sort-mark {
  font-size: 9px;
  opacity: 0.8;
}
</style>
