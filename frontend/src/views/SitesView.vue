<script setup lang="ts">
// Список сайтов: статус верификации, мини-индикаторы последнего запуска, добавление сайта.

import { computed, reactive, ref, shallowRef } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { sitesApi } from '@/api/endpoints'
import type { SiteOut, SiteSummaryOut } from '@/api/types'
import DataTable, { type Column } from '@/components/DataTable.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorMessage from '@/components/ErrorMessage.vue'
import Panel from '@/components/Panel.vue'
import Skeleton from '@/components/Skeleton.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useErrorText } from '@/composables/useErrorText'
import { useFormat } from '@/composables/useFormat'
import { useResource } from '@/composables/useResource'
import { useToastStore } from '@/stores/toasts'

const { t } = useI18n()
const fmt = useFormat()
const router = useRouter()
const toasts = useToastStore()
const { fieldErrors } = useErrorText()

// Сводки грузятся отдельно для каждого сайта; ошибки сводки не ломают список.
const summaries = shallowRef<Record<string, SiteSummaryOut | null>>({})

const sites = useResource(async () => {
  const page = await sitesApi.list()
  void loadSummaries(page.items)
  return page
})

async function loadSummaries(items: SiteOut[]): Promise<void> {
  const entries = await Promise.all(
    items.map(async (site) => {
      try {
        return [site.id, await sitesApi.summary(site.id)] as const
      } catch {
        return [site.id, null] as const
      }
    }),
  )
  summaries.value = Object.fromEntries(entries)
}

const columns = computed<Column[]>(() => [
  { key: 'name', label: t('sites.columns.site') },
  { key: 'status', label: t('sites.columns.status') },
  { key: 'p95', label: t('sites.columns.p95'), numeric: true },
  { key: 'errors', label: t('sites.columns.errorRate'), numeric: true },
  { key: 'slo', label: t('sites.columns.slo'), numeric: true },
  { key: 'lastRun', label: t('sites.columns.lastRun') },
  { key: 'actions', label: t('common.actions') },
])

const rows = computed(() => sites.data.value?.items ?? [])

const form = reactive({ url: '', name: '' })
const creating = ref(false)
const createError = ref<unknown>(null)
const createFields = computed(() => fieldErrors(createError.value))

async function addSite(): Promise<void> {
  creating.value = true
  createError.value = null
  try {
    const site = await sitesApi.create(form.url.trim(), form.name.trim() || undefined)
    toasts.success(t('sites.added'))
    form.url = ''
    form.name = ''
    await router.push(`/sites/${site.id}`)
  } catch (err) {
    createError.value = err
  } finally {
    creating.value = false
  }
}

function summaryOf(site: SiteOut): SiteSummaryOut | null | undefined {
  return summaries.value[site.id]
}
</script>

<template>
  <div class="page">
    <Panel :title="t('sites.addTitle')">
      <form class="add-form" novalidate @submit.prevent="addSite">
        <label class="field url">
          <span class="field-label">{{ t('sites.url') }}</span>
          <input
            v-model="form.url"
            class="input"
            type="url"
            required
            inputmode="url"
            :placeholder="t('sites.urlPlaceholder')"
            :aria-invalid="Boolean(createFields.url)"
          />
          <span v-if="createFields.url" class="field-error">{{ createFields.url.text }}</span>
        </label>
        <label class="field">
          <span class="field-label">{{ t('sites.name') }}</span>
          <input v-model="form.name" class="input" type="text" maxlength="100" :placeholder="t('sites.namePlaceholder')" />
        </label>
        <button type="submit" class="btn btn-primary" :disabled="creating || !form.url.trim()">
          {{ creating ? t('common.saving') : t('sites.add') }}
        </button>
      </form>
      <ErrorMessage v-if="createError" :error="createError" class="mt" />
      <p class="field-hint mt">{{ t('sites.addHint') }}</p>
    </Panel>

    <Panel :title="t('sites.listTitle')" flush>
      <template #actions>
        <button type="button" class="btn btn-sm" :disabled="sites.loading.value" @click="sites.reload()">
          {{ t('common.refresh') }}
        </button>
      </template>
      <div v-if="sites.error.value" class="pad">
        <ErrorMessage :error="sites.error.value" retry @retry="sites.reload()" />
      </div>
      <DataTable
        v-else
        :columns="columns"
        :rows="rows"
        :row-key="(row) => row.id"
        :loading="sites.loading.value"
        :caption="t('sites.listTitle')"
      >
        <template #cell-name="{ row }">
          <RouterLink :to="`/sites/${row.id}/dashboard`" class="site-name">{{ row.name }}</RouterLink>
          <div class="muted small url-text">{{ row.url }}</div>
        </template>
        <template #cell-status="{ row }">
          <StatusBadge kind="site" :status="row.verification_status" />
        </template>
        <template #cell-p95="{ row }">
          <Skeleton v-if="summaryOf(row) === undefined" :height="12" />
          <span v-else>{{ fmt.ms(summaryOf(row)?.last_run?.metrics?.latency_p95_ms) }}</span>
        </template>
        <template #cell-errors="{ row }">
          <Skeleton v-if="summaryOf(row) === undefined" :height="12" />
          <span
            v-else
            :class="{ 'text-error': (summaryOf(row)?.last_run?.metrics?.error_rate ?? 0) > 0.01 }"
          >{{ fmt.percent(summaryOf(row)?.last_run?.metrics?.error_rate) }}</span>
        </template>
        <template #cell-slo="{ row }">
          <Skeleton v-if="summaryOf(row) === undefined" :height="12" />
          <span v-else>{{ fmt.percentValue(summaryOf(row)?.slo_met_pct) }}</span>
        </template>
        <template #cell-lastRun="{ row }">
          <Skeleton v-if="summaryOf(row) === undefined" :height="12" />
          <template v-else-if="summaryOf(row)?.last_run">
            <RouterLink :to="`/runs/${summaryOf(row)?.last_run?.id}`" class="nowrap">
              {{ fmt.relative(summaryOf(row)?.last_run?.finished_at) }}
            </RouterLink>
            <span v-if="summaryOf(row)?.regression?.detected" class="badge badge-error regress">
              {{ t('sites.regression') }}
            </span>
          </template>
          <span v-else class="muted">{{ t('sites.noRuns') }}</span>
        </template>
        <template #cell-actions="{ row }">
          <div class="row">
            <RouterLink :to="`/sites/${row.id}/dashboard`" class="btn btn-sm">{{ t('nav.dashboard') }}</RouterLink>
            <RouterLink :to="`/sites/${row.id}`" class="btn btn-sm">{{ t('nav.siteSettings') }}</RouterLink>
          </div>
        </template>
        <template #empty>
          <EmptyState :title="t('sites.emptyTitle')" :text="t('sites.emptyText')" />
        </template>
      </DataTable>
    </Panel>
  </div>
</template>

<style scoped>
.add-form {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr) auto;
  gap: var(--space-2);
  align-items: end;
}

@media (max-width: 720px) {
  .add-form {
    grid-template-columns: 1fr;
  }
}

.mt {
  margin-top: var(--space-2);
}

.pad {
  padding: var(--space-3);
}

.site-name {
  font-weight: 600;
}

.url-text {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.regress {
  margin-left: var(--space-1);
}
</style>
