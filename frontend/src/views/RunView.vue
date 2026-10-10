<script setup lang="ts">
// Страница запуска: сводка, вердикт SLO, регрессии, графики, шаги, ошибки, коды, ступени, экспорт.

import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { runsApi, scenariosApi } from '@/api/endpoints'
import type { ScenarioOut } from '@/api/types'
import { useChartTheme } from '@/charts/useChartTheme'
import EmptyState from '@/components/EmptyState.vue'
import ErrorMessage from '@/components/ErrorMessage.vue'
import Icon from '@/components/Icon.vue'
import MetricChart, { type SeriesDef } from '@/components/MetricChart.vue'
import Panel from '@/components/Panel.vue'
import RegressionFindings from '@/components/RegressionFindings.vue'
import Skeleton from '@/components/Skeleton.vue'
import StatPanel from '@/components/StatPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useErrorText } from '@/composables/useErrorText'
import { useFormat } from '@/composables/useFormat'
import { useResource } from '@/composables/useResource'
import { tk } from '@/i18n'
import { RUN_SOURCE_KEYS, STOP_REASON_KEYS } from '@/i18n/labels'
import { usePreferencesStore } from '@/stores/preferences'
import { useToastStore } from '@/stores/toasts'

const POLL_MS = 3000

const route = useRoute()
const { t } = useI18n()
const fmt = useFormat()
const { errorText, codeText } = useErrorText()
const palette = useChartTheme()
const prefs = usePreferencesStore()
const toasts = useToastStore()
const runId = String(route.params.runId)

const run = useResource(() => runsApi.get(runId))
const scenario = ref<ScenarioOut | null>(null)
const busy = ref<'cancel' | 'csv' | 'pdf' | null>(null)

const active = computed(() => run.data.value?.status === 'queued' || run.data.value?.status === 'running')

// Пока запуск в очереди или идёт — опрашиваем.
let timer: number | undefined
watch(active, (isActive) => {
  window.clearInterval(timer)
  if (isActive) timer = window.setInterval(() => void run.reload(true), POLL_MS)
}, { immediate: true })
onBeforeUnmount(() => window.clearInterval(timer))

watch(
  () => run.data.value?.scenario_id,
  async (id) => {
    if (id && scenario.value?.id !== id) scenario.value = await scenariosApi.get(id).catch(() => null)
  },
)

const summary = computed(() => run.data.value?.summary ?? null)
const series = computed(() => run.data.value?.series ?? [])

async function action(kind: 'cancel' | 'csv' | 'pdf'): Promise<void> {
  busy.value = kind
  try {
    if (kind === 'cancel') {
      if (!window.confirm(t('run.cancelConfirm'))) return
      await runsApi.cancel(runId)
      toasts.info(t('run.cancelRequested'))
      await run.reload(true)
    } else if (kind === 'csv') {
      await runsApi.exportCsv(runId)
    } else {
      await runsApi.reportPdf(runId, prefs.locale)
    }
  } catch (err) {
    toasts.error(errorText(err))
  } finally {
    busy.value = null
  }
}

const stats = computed(() => {
  const s = summary.value
  const m = run.data.value?.metrics
  return [
    { title: t('stat.requests'), value: fmt.num(s?.requests ?? m?.requests, 0) },
    { title: t('stat.rps'), value: fmt.rps(s?.rps ?? m?.rps) },
    { title: t('stat.errorRate'), value: fmt.percent(s?.error_rate ?? m?.error_rate) },
    { title: t('stat.p50'), value: fmt.ms(s?.latency_ms.p50 ?? m?.latency_p50_ms) },
    { title: t('stat.p95'), value: fmt.ms(s?.latency_ms.p95 ?? m?.latency_p95_ms) },
    { title: t('stat.p99'), value: fmt.ms(s?.latency_ms.p99 ?? m?.latency_p99_ms) },
    { title: t('stat.apdex'), value: s?.apdex === null || s?.apdex === undefined ? fmt.dash.value : fmt.num(s.apdex, 2) },
    { title: t('stat.duration'), value: fmt.seconds(s?.duration_sec) },
  ]
})

const xs = computed(() => series.value.map((p) => p.t))
const secFormat = (v: number) => fmt.seconds(v)
const msFormat = (v: number) => fmt.ms(v)
const rpsFormat = (v: number) => fmt.rps(v)
const numFormat = (v: number) => fmt.num(v, 1)

const p95Series = computed<SeriesDef[]>(() => {
  const list: SeriesDef[] = [
    {
      label: t('charts.p95'),
      data: series.value.map((p, i) => ({ x: xs.value[i] ?? 0, y: p.p95_ms })),
      color: palette.value.series[1] ?? '',
    },
  ]
  const slo = scenario.value?.slo_p95_ms
  if (slo && series.value.length) {
    list.push({
      label: t('charts.sloLine', { value: fmt.ms(slo) }),
      data: [
        { x: 0, y: slo },
        { x: xs.value[xs.value.length - 1] ?? 0, y: slo },
      ],
      color: palette.value.slo,
      dashed: true,
    })
  }
  return list
})

const rpsSeries = computed<SeriesDef[]>(() => [
  {
    label: t('charts.rps'),
    data: series.value.map((p, i) => ({ x: xs.value[i] ?? 0, y: p.rps })),
    color: palette.value.series[0] ?? '',
  },
  {
    label: t('charts.errorsPerSec'),
    data: series.value.map((p, i) => ({ x: xs.value[i] ?? 0, y: Math.round(p.rps * p.error_rate * 100) / 100 })),
    color: palette.value.error,
    axis: 'y2',
  },
])

const TIMING_ROWS = [
  { key: 'dns_ms', label: tk('charts.timing.dns') },
  { key: 'connect_ms', label: tk('charts.timing.connect') },
  { key: 'tls_ms', label: tk('charts.timing.tls') },
  { key: 'ttfb_ms', label: tk('charts.timing.ttfb') },
  { key: 'transfer_ms', label: tk('charts.timing.transfer') },
  { key: 'ttfb_p95_ms', label: tk('charts.timing.ttfbP95') },
] as const

const statusCodes = computed(() =>
  Object.entries(summary.value?.status_codes ?? {}).sort(([a], [b]) => a.localeCompare(b)),
)
const totalCodes = computed(() => statusCodes.value.reduce((sum, [, n]) => sum + n, 0))

function codeTone(code: string): string {
  if (code.startsWith('2')) return 'badge-success'
  if (code.startsWith('3')) return 'badge-info'
  if (code.startsWith('4')) return 'badge-warning'
  return 'badge-error'
}

const stepLoad = computed(() => summary.value?.step_load ?? null)
const stopReason = computed(() => {
  const reason = run.data.value?.stop_reason
  if (!reason) return null
  const key = STOP_REASON_KEYS[reason]
  return key ? t(key) : reason
})

const sloVerdict = computed(() => {
  const met = run.data.value?.slo_met
  if (met === true) return { tone: 'success', text: t('run.sloVerdictMet') }
  if (met === false) return { tone: 'error', text: t('run.sloVerdictMissed') }
  return { tone: 'info', text: t('run.sloVerdictNone') }
})
</script>

<template>
  <div class="page">
    <ErrorMessage v-if="run.error.value && !run.data.value" :error="run.error.value" retry @retry="run.reload()" />
    <Skeleton v-else-if="!run.data.value" :height="40" />
    <template v-else>
      <header class="page-header">
        <div class="stack-sm">
          <div class="row">
            <StatusBadge kind="run" :status="run.data.value.status" />
            <span class="badge badge-neutral">{{ t(RUN_SOURCE_KEYS[run.data.value.source]) }}</span>
            <strong v-if="run.data.value.label">{{ run.data.value.label }}</strong>
            <span v-if="run.data.value.cancel_requested && active" class="badge badge-warning">{{ t('run.cancelRequested') }}</span>
          </div>
          <dl class="dl">
            <dt>{{ t('run.scenario') }}</dt>
            <dd>
              <RouterLink v-if="scenario" :to="`/scenarios/${scenario.id}/edit`">{{ scenario.name }}</RouterLink>
              <span v-else class="muted">{{ fmt.dash.value }}</span>
            </dd>
            <dt>{{ t('run.queuedAt') }}</dt>
            <dd class="num">{{ fmt.dateTime(run.data.value.queued_at) }}</dd>
            <dt>{{ t('run.startedAt') }}</dt>
            <dd class="num">{{ fmt.dateTime(run.data.value.started_at) }}</dd>
            <dt>{{ t('run.finishedAt') }}</dt>
            <dd class="num">{{ fmt.dateTime(run.data.value.finished_at) }}</dd>
            <dt v-if="stopReason">{{ t('run.stopReasonLabel') }}</dt>
            <dd v-if="stopReason">{{ stopReason }}</dd>
          </dl>
        </div>
        <div class="row">
          <RouterLink :to="`/sites/${run.data.value.site_id}/dashboard`" class="btn">
            <Icon name="dashboard" :size="14" />{{ t('nav.dashboard') }}
          </RouterLink>
          <button v-if="active" type="button" class="btn btn-danger" :disabled="busy !== null || run.data.value.cancel_requested" @click="action('cancel')">
            <Icon name="stop" :size="13" />{{ t('run.cancel') }}
          </button>
          <button type="button" class="btn" :disabled="busy !== null || !summary" @click="action('csv')">
            <Icon name="download" :size="13" />{{ busy === 'csv' ? t('common.loading') : t('run.exportCsv') }}
          </button>
          <button type="button" class="btn" :disabled="busy !== null || !summary" @click="action('pdf')">
            <Icon name="download" :size="13" />{{ busy === 'pdf' ? t('common.loading') : t('run.exportPdf') }}
          </button>
        </div>
      </header>

      <p v-if="active" class="callout callout-info small" role="status">
        {{ run.data.value.status === 'queued' ? t('run.queuedHint') : t('run.runningHint') }}
      </p>
      <p v-if="run.data.value.status === 'failed'" class="callout callout-error small" role="alert">
        {{ t('run.failed', { reason: codeText(run.data.value.error) || t('errors.unknown') }) }}
      </p>
      <p v-if="run.data.value.generator_saturated" class="callout callout-warning small" role="alert">
        <Icon name="warning" :size="14" />
        {{ t('run.saturatedWarning') }}
      </p>

      <template v-if="summary">
        <div class="panel-grid">
          <StatPanel v-for="stat in stats" :key="stat.title" class="span-3 compact-stat" :title="stat.title" :value="stat.value" />
        </div>

        <div class="panel-grid">
          <Panel :title="t('run.sloTitle')" class="span-6">
            <div class="callout" :class="`callout-${sloVerdict.tone}`">{{ sloVerdict.text }}</div>
            <dl v-if="scenario" class="dl mt">
              <dt>{{ t('editor.slo.p95') }}</dt>
              <dd>{{ scenario.slo_p95_ms === null ? fmt.dash.value : fmt.ms(scenario.slo_p95_ms) }}</dd>
              <dt>{{ t('editor.slo.errorRate') }}</dt>
              <dd>{{ scenario.slo_max_error_rate === null ? fmt.dash.value : fmt.percent(scenario.slo_max_error_rate) }}</dd>
            </dl>
          </Panel>
          <Panel :title="t('regression.title')" class="span-6">
            <RegressionFindings v-if="run.data.value.regression?.detected" :regression="run.data.value.regression" compact />
            <p v-else-if="run.data.value.regression" class="small muted">
              {{ t('regression.none', { n: run.data.value.regression.baseline_runs }, run.data.value.regression.baseline_runs) }}
            </p>
            <p v-else class="small muted">{{ t('regression.notCompared') }}</p>
          </Panel>
        </div>

        <div class="panel-grid">
          <Panel :title="t('charts.p95OverTime')" class="span-6">
            <EmptyState v-if="!series.length" :title="t('dashboard.noData')" compact />
            <MetricChart v-else x-type="linear" :series="p95Series" :x-format="secFormat" :y-format="msFormat" :label="t('charts.p95OverTime')" />
          </Panel>
          <Panel :title="t('charts.rpsErrorsOverTime')" class="span-6">
            <EmptyState v-if="!series.length" :title="t('dashboard.noData')" compact />
            <MetricChart
              v-else
              x-type="linear"
              :series="rpsSeries"
              :x-format="secFormat"
              :y-format="rpsFormat"
              :y2-format="numFormat"
              :y2-title="t('charts.errorsPerSec')"
              :label="t('charts.rpsErrorsOverTime')"
            />
          </Panel>
        </div>

        <Panel :title="t('run.stepsTitle')" flush>
          <EmptyState v-if="!summary.steps?.length" :title="t('dashboard.noData')" compact />
          <div v-else class="table-wrap">
            <table class="table">
              <caption class="sr-only">{{ t('run.stepsTitle') }}</caption>
              <thead>
                <tr>
                  <th scope="col">{{ t('run.step') }}</th>
                  <th scope="col" class="num">{{ t('stat.requests') }}</th>
                  <th scope="col" class="num">{{ t('stat.rps') }}</th>
                  <th scope="col" class="num">{{ t('stat.errorRate') }}</th>
                  <th scope="col" class="num">{{ t('stat.p50') }}</th>
                  <th scope="col" class="num">{{ t('stat.p95') }}</th>
                  <th scope="col" class="num">{{ t('stat.p99') }}</th>
                  <th scope="col" class="num">{{ t('charts.timing.ttfb') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="step in summary.steps" :key="step.name">
                  <td>{{ step.name }}</td>
                  <td class="num">{{ fmt.num(step.requests, 0) }}</td>
                  <td class="num">{{ fmt.rps(step.rps) }}</td>
                  <td class="num" :class="{ 'text-error': step.error_rate > 0 }">{{ fmt.percent(step.error_rate) }}</td>
                  <td class="num">{{ fmt.ms(step.latency_ms.p50) }}</td>
                  <td class="num">{{ fmt.ms(step.latency_ms.p95) }}</td>
                  <td class="num">{{ fmt.ms(step.latency_ms.p99) }}</td>
                  <td class="num">{{ fmt.ms(step.timings.ttfb_ms) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </Panel>

        <div class="panel-grid">
          <Panel :title="t('run.errorsTitle')" class="span-4" flush>
            <EmptyState v-if="!summary.errors_top.length" :title="t('run.noErrors')" compact />
            <table v-else class="table">
              <caption class="sr-only">{{ t('run.errorsTitle') }}</caption>
              <thead>
                <tr>
                  <th scope="col">{{ t('run.errorType') }}</th>
                  <th scope="col" class="num">{{ t('run.count') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="e in summary.errors_top" :key="e.type">
                  <td>{{ codeText(e.type) }} <code class="small">{{ e.type }}</code></td>
                  <td class="num">{{ fmt.num(e.count, 0) }}</td>
                </tr>
              </tbody>
            </table>
          </Panel>
          <Panel :title="t('run.codesTitle')" class="span-4" flush>
            <EmptyState v-if="!statusCodes.length" :title="t('dashboard.noData')" compact />
            <table v-else class="table">
              <caption class="sr-only">{{ t('run.codesTitle') }}</caption>
              <thead>
                <tr>
                  <th scope="col">{{ t('run.code') }}</th>
                  <th scope="col" class="num">{{ t('run.count') }}</th>
                  <th scope="col" class="num">{{ t('run.share') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="[code, count] in statusCodes" :key="code">
                  <td><span class="badge" :class="codeTone(code)">{{ code }}</span></td>
                  <td class="num">{{ fmt.num(count, 0) }}</td>
                  <td class="num">{{ fmt.percent(count / (totalCodes || 1)) }}</td>
                </tr>
              </tbody>
            </table>
          </Panel>
          <Panel :title="t('charts.timingsTitle')" class="span-4" flush>
            <table class="table">
              <caption class="sr-only">{{ t('charts.timingsTitle') }}</caption>
              <tbody>
                <tr v-for="row in TIMING_ROWS" :key="row.key">
                  <th scope="row">{{ t(row.label) }}</th>
                  <td class="num">{{ fmt.ms(summary.timings[row.key]) }}</td>
                </tr>
              </tbody>
            </table>
          </Panel>
        </div>

        <Panel v-if="stepLoad" :title="t('charts.stepLoadTitle')" flush>
          <template #actions>
            <span class="small">
              {{ t('stat.ceiling') }}:
              <strong>{{ stepLoad.ceiling_rps === null ? fmt.dash.value : stepLoad.ceiling_is_lower_bound ? t('stat.atLeast', { value: fmt.rps(stepLoad.ceiling_rps) }) : fmt.rps(stepLoad.ceiling_rps) }}</strong>
              · {{ STOP_REASON_KEYS[stepLoad.reason] ? t(STOP_REASON_KEYS[stepLoad.reason] ?? '') : stepLoad.reason }}
            </span>
          </template>
          <div class="table-wrap">
            <table class="table">
              <caption class="sr-only">{{ t('charts.stepLoadTitle') }}</caption>
              <thead>
                <tr>
                  <th scope="col">{{ t('run.loadStep') }}</th>
                  <th scope="col" class="num">{{ t('charts.targetRps') }}</th>
                  <th scope="col" class="num">{{ t('charts.achievedRps') }}</th>
                  <th scope="col" class="num">{{ t('stat.errorRate') }}</th>
                  <th scope="col" class="num">{{ t('stat.p95') }}</th>
                  <th scope="col">{{ t('run.stepResult') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="st in stepLoad.steps" :key="st.step">
                  <td>{{ t('charts.stepN', { n: st.step + 1 }) }}</td>
                  <td class="num">{{ fmt.rps(st.target_rate) }}</td>
                  <td class="num">{{ fmt.rps(st.rps) }}</td>
                  <td class="num">{{ fmt.percent(st.error_rate) }}</td>
                  <td class="num">{{ fmt.ms(st.p95_ms) }}</td>
                  <td>
                    <span class="badge" :class="st.passed ? 'badge-success' : 'badge-error'">
                      {{ st.passed ? t('run.stepPassed') : t('run.stepFailed') }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </Panel>
      </template>
      <EmptyState v-else-if="!active" :title="t('run.noSummary')" />
    </template>
  </div>
</template>

<style scoped>
.compact-stat {
  min-height: 0;
}

.mt {
  margin-top: var(--space-2);
}
</style>
