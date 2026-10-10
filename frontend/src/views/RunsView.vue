<script setup lang="ts">
// История запусков: фильтры (статус, сценарий, источник) на сервере, страницы, сортировка колонок.

import { computed, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { runsApi, scenariosApi } from '@/api/endpoints'
import type { RunOut, RunSource, RunStatus } from '@/api/types'
import DataTable, { type Column, type SortState } from '@/components/DataTable.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorMessage from '@/components/ErrorMessage.vue'
import Icon from '@/components/Icon.vue'
import Pagination from '@/components/Pagination.vue'
import Panel from '@/components/Panel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useFormat } from '@/composables/useFormat'
import { useResource } from '@/composables/useResource'
import { RUN_SOURCE_KEYS, RUN_STATUS_KEYS } from '@/i18n/labels'

const PAGE_SIZE = 25

const route = useRoute()
const { t } = useI18n()
const fmt = useFormat()
const siteId = String(route.params.siteId)

const filters = reactive({
  status: '' as RunStatus | '',
  scenario_id: '',
  source: '' as RunSource | '',
})
const offset = ref(0)
const sort = ref<SortState | null>({ key: 'queued_at', dir: 'desc' })

const scenarios = useResource(() => scenariosApi.list(siteId))
const scenarioNames = computed(
  () => new Map((scenarios.data.value?.items ?? []).map((s) => [s.id, s.name] as const)),
)

const runs = useResource(() =>
  runsApi.list(siteId, { ...filters, limit: PAGE_SIZE, offset: offset.value }),
)

watch(filters, () => {
  offset.value = 0
  void runs.reload()
})

function goTo(next: number): void {
  offset.value = Math.max(0, next)
  void runs.reload()
}

const columns = computed<Column[]>(() => [
  { key: 'queued_at', label: t('runs.columns.started'), sortable: true },
  { key: 'status', label: t('runs.columns.status'), sortable: true },
  { key: 'scenario', label: t('runs.columns.scenario'), sortable: true },
  { key: 'source', label: t('runs.columns.source'), sortable: true },
  { key: 'duration', label: t('runs.columns.duration'), numeric: true },
  { key: 'p95', label: t('runs.columns.p95'), numeric: true, sortable: true },
  { key: 'rps', label: t('runs.columns.rps'), numeric: true, sortable: true },
  { key: 'errorRate', label: t('runs.columns.errorRate'), numeric: true, sortable: true },
  { key: 'flags', label: t('runs.columns.flags') },
])

function sortValue(run: RunOut, key: string): string | number {
  switch (key) {
    case 'queued_at':
      return run.started_at ?? run.queued_at
    case 'status':
      return t(RUN_STATUS_KEYS[run.status])
    case 'scenario':
      return scenarioNames.value.get(run.scenario_id ?? '') ?? ''
    case 'source':
      return t(RUN_SOURCE_KEYS[run.source])
    case 'p95':
      return run.metrics?.latency_p95_ms ?? -1
    case 'rps':
      return run.metrics?.rps ?? -1
    case 'errorRate':
      return run.metrics?.error_rate ?? -1
    default:
      return ''
  }
}

// Сервер отдаёт страницу от новых к старым; сортировка по колонкам — внутри страницы.
const rows = computed(() => {
  const items = [...(runs.data.value?.items ?? [])]
  const s = sort.value
  if (!s) return items
  const dir = s.dir === 'asc' ? 1 : -1
  return items.sort((a, b) => {
    const va = sortValue(a, s.key)
    const vb = sortValue(b, s.key)
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir
    return String(va).localeCompare(String(vb)) * dir
  })
})

const STATUSES: RunStatus[] = ['queued', 'running', 'finished', 'failed', 'cancelled']
const SOURCES: RunSource[] = ['manual', 'schedule', 'hook']
</script>

<template>
  <div class="page">
    <Panel :title="t('runs.title')" flush>
      <template #actions>
        <RouterLink :to="`/sites/${siteId}/dashboard`" class="btn btn-sm">{{ t('nav.dashboard') }}</RouterLink>
      </template>
      <form class="filters" :aria-label="t('runs.filters')" @submit.prevent>
        <label class="field">
          <span class="field-label">{{ t('runs.columns.status') }}</span>
          <select v-model="filters.status" class="select">
            <option value="">{{ t('runs.any') }}</option>
            <option v-for="s in STATUSES" :key="s" :value="s">{{ t(RUN_STATUS_KEYS[s]) }}</option>
          </select>
        </label>
        <label class="field">
          <span class="field-label">{{ t('runs.columns.scenario') }}</span>
          <select v-model="filters.scenario_id" class="select">
            <option value="">{{ t('runs.any') }}</option>
            <option v-for="s in scenarios.data.value?.items ?? []" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </label>
        <label class="field">
          <span class="field-label">{{ t('runs.columns.source') }}</span>
          <select v-model="filters.source" class="select">
            <option value="">{{ t('runs.any') }}</option>
            <option v-for="s in SOURCES" :key="s" :value="s">{{ t(RUN_SOURCE_KEYS[s]) }}</option>
          </select>
        </label>
        <button type="button" class="btn" :disabled="runs.loading.value" @click="runs.reload()">
          <Icon name="refresh" :size="13" />{{ t('common.refresh') }}
        </button>
      </form>
      <div v-if="runs.error.value" class="pad">
        <ErrorMessage :error="runs.error.value" retry @retry="runs.reload()" />
      </div>
      <template v-else>
        <DataTable
          v-model:sort="sort"
          :columns="columns"
          :rows="rows"
          :row-key="(r) => r.id"
          :loading="runs.loading.value"
          :caption="t('runs.title')"
        >
          <template #cell-queued_at="{ row }">
            <RouterLink :to="`/runs/${row.id}`" class="nowrap">{{ fmt.dateTime(row.started_at ?? row.queued_at) }}</RouterLink>
            <div v-if="row.label" class="small muted">{{ row.label }}</div>
          </template>
          <template #cell-status="{ row }"><StatusBadge kind="run" :status="row.status" /></template>
          <template #cell-scenario="{ row }">{{ scenarioNames.get(row.scenario_id ?? '') ?? fmt.dash.value }}</template>
          <template #cell-source="{ row }">{{ t(RUN_SOURCE_KEYS[row.source]) }}</template>
          <template #cell-duration="{ row }">{{ fmt.duration(row.started_at, row.finished_at) }}</template>
          <template #cell-p95="{ row }">{{ fmt.ms(row.metrics?.latency_p95_ms) }}</template>
          <template #cell-rps="{ row }">{{ fmt.rps(row.metrics?.rps) }}</template>
          <template #cell-errorRate="{ row }">{{ fmt.percent(row.metrics?.error_rate) }}</template>
          <template #cell-flags="{ row }">
            <div class="row">
              <span v-if="row.generator_saturated" class="badge badge-warning" :title="t('run.saturatedWarning')">
                <Icon name="warning" :size="11" />{{ t('run.saturated') }}
              </span>
              <span v-if="row.regression?.detected" class="badge badge-error">{{ t('sites.regression') }}</span>
              <span v-if="row.slo_met === true" class="badge badge-success">{{ t('run.sloMet') }}</span>
              <span v-else-if="row.slo_met === false" class="badge badge-error">{{ t('run.sloMissed') }}</span>
            </div>
          </template>
          <template #empty>
            <EmptyState :title="t('runs.emptyTitle')" :text="t('runs.emptyText')" />
          </template>
        </DataTable>
        <Pagination
          v-if="runs.data.value && runs.data.value.total > 0"
          :total="runs.data.value.total"
          :limit="PAGE_SIZE"
          :offset="offset"
          @change="goTo"
        />
      </template>
    </Panel>
  </div>
</template>

<style scoped>
.filters {
  display: flex;
  gap: var(--space-2);
  align-items: flex-end;
  flex-wrap: wrap;
  padding: var(--space-3);
  border-bottom: 1px solid var(--border);
}

.filters .field {
  min-width: 150px;
}

.pad {
  padding: var(--space-3);
}
</style>
