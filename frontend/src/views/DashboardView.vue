<script setup lang="ts">
// Дашборд сайта: stat-панели, графики по запускам за период, аннотации, алерты.

import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { runsApi, scenariosApi, sitesApi } from '@/api/endpoints'
import type {
  AlertOut,
  AnnotationOut,
  MetricPoint,
  RunDetailOut,
  ScenarioOut,
  SiteMetricsOut,
  SiteSummaryOut,
} from '@/api/types'
import type { ChartMark } from '@/charts/annotations'
import { useChartTheme } from '@/charts/useChartTheme'
import EmptyState from '@/components/EmptyState.vue'
import ErrorMessage from '@/components/ErrorMessage.vue'
import Icon from '@/components/Icon.vue'
import MetricChart, { type SeriesDef } from '@/components/MetricChart.vue'
import Panel from '@/components/Panel.vue'
import Skeleton from '@/components/Skeleton.vue'
import StatPanel, { type StatLevel } from '@/components/StatPanel.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import TimeRangePicker from '@/components/TimeRangePicker.vue'
import RegressionFindings from '@/components/RegressionFindings.vue'
import { useErrorText } from '@/composables/useErrorText'
import { useFormat } from '@/composables/useFormat'
import { useResource } from '@/composables/useResource'
import { REFRESH_MS, REFRESH_OPTIONS, type RefreshKey, useTimeRange } from '@/composables/useTimeRange'
import { tk } from '@/i18n'
import { ALERT_TYPE_KEYS, RUN_SOURCE_KEYS } from '@/i18n/labels'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const fmt = useFormat()
const { codeText } = useErrorText()
const palette = useChartTheme()
const tr = useTimeRange()
const siteId = String(route.params.siteId)

const REFRESH_KEYS: Record<RefreshKey, string> = {
  off: tk('refresh.off'),
  '30s': tk('refresh.30s'),
  '1m': tk('refresh.1m'),
  '5m': tk('refresh.5m'),
}

const sites = useResource(() => sitesApi.list())
const site = useResource(() => sitesApi.get(siteId))

// Данные дашборда грузятся одним заходом; фоновые обновления не показывают скелетоны.
const summary = shallowRef<SiteSummaryOut | null>(null)
const metrics = shallowRef<SiteMetricsOut | null>(null)
const annotations = shallowRef<AnnotationOut[]>([])
const alerts = shallowRef<AlertOut[]>([])
const scenario = shallowRef<ScenarioOut | null>(null)
const lastRun = shallowRef<RunDetailOut | null>(null)
const stepRun = shallowRef<RunDetailOut | null>(null)
const loading = ref(true)
const refreshing = ref(false)
const error = ref<unknown>(null)
const scenarioCache = new Map<string, ScenarioOut | null>()
const runCache = new Map<string, RunDetailOut>()
let loadSeq = 0

async function cachedScenario(id: string | null | undefined): Promise<ScenarioOut | null> {
  if (!id) return null
  if (!scenarioCache.has(id)) {
    scenarioCache.set(id, await scenariosApi.get(id).catch(() => null))
  }
  return scenarioCache.get(id) ?? null
}

async function cachedRun(id: string | null | undefined): Promise<RunDetailOut | null> {
  if (!id) return null
  const known = runCache.get(id)
  if (known) return known
  const run = await runsApi.get(id).catch(() => null)
  if (run) runCache.set(id, run)
  return run
}

async function load(silent = false): Promise<void> {
  const seq = ++loadSeq
  tr.touch()
  if (silent) refreshing.value = true
  else loading.value = true
  const from = tr.range.value.from.toISOString()
  const to = tr.range.value.to.toISOString()
  try {
    const [s, m, a, al] = await Promise.all([
      sitesApi.summary(siteId, tr.periodDays.value),
      sitesApi.metrics(siteId, from, to),
      sitesApi.annotations(siteId, from, to),
      sitesApi.alerts(siteId, 'open'),
    ])
    // Запуск со ступенчатым профилем — самый свежий в периоде с найденным потолком.
    const stepPoint = [...m.points].reverse().find((p) => p.ceiling_rps !== null)
    const [sc, last, step] = await Promise.all([
      cachedScenario(s.last_run?.scenario_id),
      cachedRun(s.last_run?.id),
      cachedRun(stepPoint?.run_id),
    ])
    if (seq !== loadSeq) return
    summary.value = s
    metrics.value = m
    annotations.value = a
    alerts.value = al
    scenario.value = sc
    lastRun.value = last
    stepRun.value = step?.summary?.step_load ? step : last?.summary?.step_load ? last : null
    error.value = null
  } catch (err) {
    if (seq === loadSeq) error.value = err
  } finally {
    if (seq === loadSeq) {
      loading.value = false
      refreshing.value = false
    }
  }
}

watch(
  () => [tr.rangeKey.value, route.query.from, route.query.to],
  () => void load(),
  { immediate: true },
)

// Автообновление: интервал из URL, вкладка в фоне не дёргает API.
let timer: number | undefined
watch(
  tr.refreshKey,
  (key) => {
    window.clearInterval(timer)
    const ms = REFRESH_MS[key]
    if (ms) {
      timer = window.setInterval(() => {
        if (document.visibilityState === 'visible') void load(true)
      }, ms)
    }
  },
  { immediate: true },
)
onBeforeUnmount(() => window.clearInterval(timer))

function switchSite(event: Event): void {
  const id = (event.target as HTMLSelectElement).value
  if (id && id !== siteId) void router.push({ path: `/sites/${id}/dashboard`, query: route.query })
}

// ---- stat-панели ----

const last = computed(() => summary.value?.last_run ?? null)
const sloP95 = computed(() => scenario.value?.slo_p95_ms ?? null)
const sloErr = computed(() => scenario.value?.slo_max_error_rate ?? null)

function p95Level(value: number | undefined): StatLevel {
  if (value === undefined) return 'neutral'
  if (sloP95.value === null) return 'neutral'
  if (value <= sloP95.value * 0.8) return 'ok'
  return value <= sloP95.value ? 'warn' : 'bad'
}

function errorLevel(value: number | undefined): StatLevel {
  if (value === undefined) return 'neutral'
  if (sloErr.value !== null) return value <= sloErr.value ? (value <= sloErr.value / 2 ? 'ok' : 'warn') : 'bad'
  if (value <= 0.01) return 'ok'
  return value <= 0.05 ? 'warn' : 'bad'
}

function apdexLevel(value: number | null | undefined): StatLevel {
  if (value === null || value === undefined) return 'neutral'
  if (value >= 0.85) return 'ok'
  return value >= 0.7 ? 'warn' : 'bad'
}

function sloPctLevel(value: number | null | undefined): StatLevel {
  if (value === null || value === undefined) return 'neutral'
  if (value >= 99) return 'ok'
  return value >= 90 ? 'warn' : 'bad'
}

const LEVEL_KEYS: Record<StatLevel, string> = {
  ok: tk('stat.level.ok'),
  warn: tk('stat.level.warn'),
  bad: tk('stat.level.bad'),
  neutral: tk('stat.level.neutral'),
}

const stats = computed(() => {
  const m = last.value?.metrics
  const s = summary.value
  const p95 = p95Level(m?.latency_p95_ms)
  const err = errorLevel(m?.error_rate)
  const apdex = apdexLevel(m?.apdex)
  const slo = sloPctLevel(s?.slo_met_pct)
  const ceiling = s?.ceiling_rps ?? null
  return [
    {
      title: t('stat.p95'),
      value: fmt.ms(m?.latency_p95_ms),
      level: p95,
      hint: sloP95.value !== null ? t('stat.sloTarget', { value: fmt.ms(sloP95.value) }) : t('stat.noSlo'),
    },
    {
      title: t('stat.errorRate'),
      value: fmt.percent(m?.error_rate),
      level: err,
      hint: sloErr.value !== null ? t('stat.sloTarget', { value: fmt.percent(sloErr.value) }) : t('stat.noSlo'),
    },
    {
      title: t('stat.rps'),
      value: fmt.rps(m?.rps),
      level: 'neutral' as StatLevel,
      hint: m ? t('stat.requestsCount', { n: fmt.num(m.requests, 0) }) : '',
    },
    {
      title: t('stat.apdex'),
      value: m?.apdex === null || m?.apdex === undefined ? fmt.dash.value : fmt.num(m.apdex, 2),
      level: apdex,
      hint: scenario.value ? t('stat.apdexT', { value: fmt.ms(scenario.value.apdex_t_ms) }) : '',
    },
    {
      title: t('stat.sloPct'),
      value: fmt.percentValue(s?.slo_met_pct),
      level: slo,
      hint: s ? t('stat.runsInDays', { n: s.runs_in_period, days: s.period_days }, s.runs_in_period) : '',
    },
    {
      title: t('stat.ceiling'),
      value:
        ceiling === null
          ? fmt.dash.value
          : s?.ceiling_is_lower_bound
            ? t('stat.atLeast', { value: fmt.rps(ceiling) })
            : fmt.rps(ceiling),
      level: 'neutral' as StatLevel,
      hint: s?.ceiling_is_lower_bound ? t('stat.ceilingLowerBound') : t('stat.ceilingHint'),
    },
  ]
})

// ---- графики ----

const points = computed<MetricPoint[]>(() => metrics.value?.points ?? [])
const xs = computed(() => points.value.map((p) => new Date(p.started_at).getTime()))
const xMin = computed(() => tr.range.value.from.getTime())
const xMax = computed(() => tr.range.value.to.getTime())
const saturatedCount = computed(() => points.value.filter((p) => p.generator_saturated).length)
const regressionPoints = computed(() => points.value.filter((p) => p.regression_detected))

function series(pick: (p: MetricPoint) => number | null): { x: number; y: number | null }[] {
  return points.value.map((p, i) => ({ x: xs.value[i] ?? 0, y: pick(p) }))
}

const marks = computed<ChartMark[]>(() => {
  const p = palette.value
  const list: ChartMark[] = annotations.value.map((a) => ({
    x: new Date(a.at).getTime(),
    color: a.kind === 'deploy' ? p.deploy : p.comment,
    label: a.label,
    dashed: a.kind === 'comment',
  }))
  for (const point of regressionPoints.value) {
    list.push({
      x: new Date(point.started_at).getTime(),
      color: p.regression,
      shape: 'diamond',
      dashed: true,
    })
  }
  return list
})

const latencySeries = computed<SeriesDef[]>(() => {
  const p = palette.value
  const s = p.series
  const warnPoints = points.value.map((pt) => (pt.generator_saturated ? p.warning : (s[1] ?? '')))
  const styles = points.value.map((pt) => (pt.generator_saturated ? 'triangle' : 'circle') as 'triangle' | 'circle')
  const radius = points.value.map((pt) => (pt.generator_saturated ? 5 : 2))
  const list: SeriesDef[] = [
    { label: t('charts.p50'), data: series((pt) => pt.latency_p50_ms), color: s[0] ?? '' },
    {
      label: t('charts.p95'),
      data: series((pt) => pt.latency_p95_ms),
      color: s[1] ?? '',
      pointColors: warnPoints,
      pointStyles: styles,
      pointRadius: radius,
    },
    { label: t('charts.p99'), data: series((pt) => pt.latency_p99_ms), color: s[3] ?? '' },
  ]
  if (sloP95.value !== null) {
    list.push({
      label: t('charts.sloLine', { value: fmt.ms(sloP95.value) }),
      data: [
        { x: xMin.value, y: sloP95.value },
        { x: xMax.value, y: sloP95.value },
      ],
      color: p.slo,
      dashed: true,
    })
  }
  return list
})

const rpsSeries = computed<SeriesDef[]>(() => [
  { label: t('charts.rps'), data: series((pt) => pt.rps), color: palette.value.series[0] ?? '' },
  {
    label: t('charts.errorRate'),
    data: series((pt) => pt.error_rate),
    color: palette.value.error,
    axis: 'y2',
  },
])

const CODE_CLASSES = ['2', '3', '4', '5'] as const
const codeSeries = computed<SeriesDef[]>(() => {
  const p = palette.value
  const colors: Record<(typeof CODE_CLASSES)[number], string> = {
    '2': p.success,
    '3': p.info,
    '4': p.warning,
    '5': p.error,
  }
  return CODE_CLASSES.map((cls) => ({
    label: t('charts.codeClass', { cls }),
    data: series((pt) =>
      Object.entries(pt.status_codes)
        .filter(([code]) => code.startsWith(cls))
        .reduce((sum, [, count]) => sum + count, 0),
    ),
    color: colors[cls],
    type: 'bar' as const,
    stack: 'codes',
  }))
})

const TIMING_PARTS = [
  { key: 'dns_ms', label: tk('charts.timing.dns'), color: 0 },
  { key: 'connect_ms', label: tk('charts.timing.connect'), color: 5 },
  { key: 'tls_ms', label: tk('charts.timing.tls'), color: 3 },
  { key: 'ttfb_ms', label: tk('charts.timing.ttfb'), color: 1 },
  { key: 'transfer_ms', label: tk('charts.timing.transfer'), color: 2 },
] as const

const timingSeries = computed<SeriesDef[]>(() =>
  TIMING_PARTS.map((part) => ({
    label: t(part.label),
    data: series((pt) => pt.timings[part.key] ?? 0),
    color: palette.value.series[part.color] ?? '',
    type: 'bar' as const,
    stack: 'timings',
  })),
)

const steps = computed(() => lastRun.value?.summary?.steps ?? [])
const stepSeries = computed<SeriesDef[]>(() => {
  const s = palette.value.series
  return [
    { label: t('charts.p50'), data: steps.value.map((st) => st.latency_ms.p50), color: s[0] ?? '', type: 'bar' },
    { label: t('charts.p95'), data: steps.value.map((st) => st.latency_ms.p95), color: s[1] ?? '', type: 'bar' },
    { label: t('charts.p99'), data: steps.value.map((st) => st.latency_ms.p99), color: s[3] ?? '', type: 'bar' },
  ]
})

const stepLoad = computed(() => stepRun.value?.summary?.step_load ?? null)
const stepLoadLabels = computed(() =>
  (stepLoad.value?.steps ?? []).map((st) => t('charts.stepN', { n: st.step + 1 })),
)
const stepLoadSeries = computed<SeriesDef[]>(() => {
  const sl = stepLoad.value
  if (!sl) return []
  const p = palette.value
  const list: SeriesDef[] = [
    {
      label: t('charts.achievedRps'),
      data: sl.steps.map((st) => st.rps),
      color: p.series[0] ?? '',
      type: 'bar',
      pointColors: sl.steps.map((st) => (st.passed ? (p.series[0] ?? '') : p.error)),
    },
    {
      label: t('charts.targetRps'),
      data: sl.steps.map((st) => st.target_rate),
      color: p.muted,
      type: 'line',
      dashed: true,
    },
    {
      label: t('charts.errorRate'),
      data: sl.steps.map((st) => st.error_rate),
      color: p.error,
      type: 'line',
      axis: 'y2',
    },
  ]
  if (sl.ceiling_rps !== null) {
    list.push({
      label: t('charts.ceilingLine', { value: fmt.rps(sl.ceiling_rps) }),
      data: sl.steps.map(() => sl.ceiling_rps),
      color: p.slo,
      type: 'line',
      dashed: true,
    })
  }
  return list
})

const recentRuns = computed(() => [...points.value].reverse().slice(0, 10))
const hasData = computed(() => points.value.length > 0)
const msFormat = (v: number) => fmt.ms(v)
const pctFormat = (v: number) => fmt.percent(v)
const rpsFormat = (v: number) => fmt.rps(v)
const intFormat = (v: number) => fmt.num(v, 0)

function alertRegression(alert: AlertOut) {
  const reg = alert.details?.regression
  return reg && typeof reg === 'object' ? (reg as SiteSummaryOut['regression']) : null
}
</script>

<template>
  <Teleport to="#topbar-controls" defer>
    <label class="sr-only" for="site-select">{{ t('dashboard.site') }}</label>
    <select id="site-select" class="select site-select" :value="siteId" @change="switchSite">
      <option v-if="!sites.data.value" :value="siteId">{{ site.data.value?.name ?? t('common.loading') }}</option>
      <option v-for="s in sites.data.value?.items ?? []" :key="s.id" :value="s.id">{{ s.name }}</option>
    </select>
    <TimeRangePicker
      :range-key="tr.rangeKey.value"
      :from="tr.range.value.from"
      :to="tr.range.value.to"
      @preset="tr.setPreset"
      @custom="tr.setCustom"
    />
    <button
      type="button"
      class="btn btn-icon"
      :aria-label="t('dashboard.refresh')"
      :title="t('dashboard.refresh')"
      :disabled="loading || refreshing"
      @click="load()"
    >
      <Icon name="refresh" :class="{ spin: refreshing }" />
    </button>
    <label class="sr-only" for="refresh-select">{{ t('refresh.label') }}</label>
    <select
      id="refresh-select"
      class="select refresh-select"
      :value="tr.refreshKey.value"
      :title="t('refresh.label')"
      @change="tr.setRefresh(($event.target as HTMLSelectElement).value as RefreshKey)"
    >
      <option v-for="key in REFRESH_OPTIONS" :key="key" :value="key">{{ t(REFRESH_KEYS[key]) }}</option>
    </select>
  </Teleport>

  <div class="page dashboard">
    <ErrorMessage v-if="error" :error="error" retry @retry="load()" />

    <div v-for="alert in alerts" :key="alert.id" class="callout callout-error alert-banner" role="alert">
      <Icon name="warning" />
      <div class="stack-sm grow">
        <div class="row-between">
          <strong>{{ t(ALERT_TYPE_KEYS[alert.type]) }}</strong>
          <span class="small muted">{{ t('alerts.openedAt', { date: fmt.dateTime(alert.opened_at) }) }}</span>
        </div>
        <RegressionFindings v-if="alertRegression(alert)" :regression="alertRegression(alert)" compact />
        <span v-if="typeof alert.details?.error === 'string'" class="small">{{ codeText(alert.details.error) }}</span>
        <RouterLink v-if="alert.run_id" :to="`/runs/${alert.run_id}`" class="small">{{ t('alerts.openRun') }}</RouterLink>
      </div>
    </div>

    <p v-if="site.data.value && site.data.value.verification_status !== 'verified'" class="callout callout-warning small">
      {{ t('dashboard.notVerified') }}
      <RouterLink :to="`/sites/${siteId}`">{{ t('dashboard.goVerify') }}</RouterLink>
    </p>

    <p v-if="saturatedCount" class="callout callout-warning small">
      <Icon name="warning" :size="14" />
      {{ t('dashboard.saturatedRuns', { n: saturatedCount }, saturatedCount) }}
    </p>
    <p v-if="metrics?.truncated" class="callout callout-info small">{{ t('dashboard.truncated') }}</p>

    <div class="panel-grid">
      <StatPanel
        v-for="stat in stats"
        :key="stat.title"
        class="span-2"
        :title="stat.title"
        :value="stat.value"
        :level="stat.level"
        :level-text="t(LEVEL_KEYS[stat.level])"
        :hint="stat.hint"
        :loading="loading && !summary"
      />
    </div>

    <RegressionFindings
      v-if="summary?.regression?.detected && !alerts.some((a) => a.type === 'regression')"
      :regression="summary.regression"
      class="callout callout-error"
    />

    <div class="panel-grid">
      <Panel :title="t('charts.latencyTitle')" class="span-6">
        <Skeleton v-if="loading && !metrics" :height="220" />
        <EmptyState v-else-if="!hasData" :title="t('dashboard.noData')" :text="t('dashboard.noDataText')" compact />
        <MetricChart
          v-else
          :series="latencySeries"
          :marks="marks"
          :x-min="xMin"
          :x-max="xMax"
          :y-format="msFormat"
          :label="t('charts.latencyTitle')"
        />
      </Panel>
      <Panel :title="t('charts.rpsTitle')" class="span-6">
        <Skeleton v-if="loading && !metrics" :height="220" />
        <EmptyState v-else-if="!hasData" :title="t('dashboard.noData')" compact />
        <MetricChart
          v-else
          :series="rpsSeries"
          :marks="marks"
          :x-min="xMin"
          :x-max="xMax"
          :y-format="rpsFormat"
          :y2-format="pctFormat"
          :label="t('charts.rpsTitle')"
        />
      </Panel>
      <Panel :title="t('charts.codesTitle')" class="span-6">
        <Skeleton v-if="loading && !metrics" :height="220" />
        <EmptyState v-else-if="!hasData" :title="t('dashboard.noData')" compact />
        <MetricChart
          v-else
          kind="bar"
          stacked
          :series="codeSeries"
          :marks="marks"
          :x-min="xMin"
          :x-max="xMax"
          :y-format="intFormat"
          :label="t('charts.codesTitle')"
        />
      </Panel>
      <Panel :title="t('charts.timingsTitle')" :description="t('charts.timingsHint')" class="span-6">
        <Skeleton v-if="loading && !metrics" :height="220" />
        <EmptyState v-else-if="!hasData" :title="t('dashboard.noData')" compact />
        <MetricChart
          v-else
          kind="bar"
          stacked
          :series="timingSeries"
          :marks="marks"
          :x-min="xMin"
          :x-max="xMax"
          :y-format="msFormat"
          :label="t('charts.timingsTitle')"
        />
      </Panel>
      <div v-if="marks.length" class="span-12 marks-legend small muted" :aria-label="t('charts.marksLegend')">
        <span class="legend-item"><span class="swatch deploy" />{{ t('charts.markDeploy') }}</span>
        <span class="legend-item"><span class="swatch comment" />{{ t('charts.markComment') }}</span>
        <span class="legend-item"><span class="swatch regression" />{{ t('charts.markRegression') }}</span>
        <span class="legend-item"><span class="swatch saturated" />{{ t('charts.markSaturated') }}</span>
        <ul class="annotation-list">
          <li v-for="a in annotations" :key="a.id">{{ fmt.dateTimeShort(a.at) }} — {{ a.label }}</li>
        </ul>
      </div>
      <Panel :title="t('charts.stepsTitle')" :description="lastRun ? t('charts.fromLastRun') : undefined" class="span-6">
        <Skeleton v-if="loading && !summary" :height="220" />
        <EmptyState v-else-if="!steps.length" :title="t('dashboard.noData')" compact />
        <MetricChart
          v-else
          kind="bar"
          x-type="category"
          :labels="steps.map((s) => s.name)"
          :series="stepSeries"
          :y-format="msFormat"
          :label="t('charts.stepsTitle')"
        />
      </Panel>
      <Panel :title="t('charts.stepLoadTitle')" class="span-6">
        <template v-if="stepRun" #actions>
          <RouterLink :to="`/runs/${stepRun.id}`" class="small">{{ fmt.dateTimeShort(stepRun.started_at) }}</RouterLink>
        </template>
        <Skeleton v-if="loading && !summary" :height="220" />
        <EmptyState v-else-if="!stepLoad" :title="t('charts.noStepLoad')" :text="t('charts.noStepLoadText')" compact />
        <template v-else>
          <p v-if="stepRun?.generator_saturated" class="callout callout-warning small sat">
            {{ t('run.saturatedWarning') }}
          </p>
          <MetricChart
            kind="bar"
            x-type="category"
            :labels="stepLoadLabels"
            :series="stepLoadSeries"
            :y-format="rpsFormat"
            :y2-format="pctFormat"
            :label="t('charts.stepLoadTitle')"
          />
        </template>
      </Panel>

      <Panel :title="t('dashboard.recentRuns')" class="span-12" flush>
        <template #actions>
          <RouterLink :to="`/sites/${siteId}/runs`" class="btn btn-sm">{{ t('dashboard.allRuns') }}</RouterLink>
        </template>
        <div v-if="loading && !metrics" class="pad"><Skeleton :lines="4" /></div>
        <EmptyState v-else-if="!recentRuns.length" :title="t('dashboard.noData')" :text="t('dashboard.noDataText')" compact />
        <div v-else class="table-wrap">
          <table class="table">
            <caption class="sr-only">{{ t('dashboard.recentRuns') }}</caption>
            <thead>
              <tr>
                <th scope="col">{{ t('runs.columns.started') }}</th>
                <th scope="col">{{ t('runs.columns.source') }}</th>
                <th scope="col" class="num">{{ t('runs.columns.p95') }}</th>
                <th scope="col" class="num">{{ t('runs.columns.rps') }}</th>
                <th scope="col" class="num">{{ t('runs.columns.errorRate') }}</th>
                <th scope="col">{{ t('runs.columns.flags') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in recentRuns" :key="p.run_id">
                <td><RouterLink :to="`/runs/${p.run_id}`">{{ fmt.dateTime(p.started_at) }}</RouterLink></td>
                <td>{{ t(RUN_SOURCE_KEYS[p.source]) }}<span v-if="p.label" class="muted"> · {{ p.label }}</span></td>
                <td class="num">{{ fmt.ms(p.latency_p95_ms) }}</td>
                <td class="num">{{ fmt.rps(p.rps) }}</td>
                <td class="num">{{ fmt.percent(p.error_rate) }}</td>
                <td class="row">
                  <span v-if="p.generator_saturated" class="badge badge-warning" :title="t('run.saturatedWarning')">
                    <Icon name="warning" :size="11" />{{ t('run.saturated') }}
                  </span>
                  <span v-if="p.regression_detected" class="badge badge-error">{{ t('sites.regression') }}</span>
                  <span v-if="p.slo_met === true" class="badge badge-success">{{ t('run.sloMet') }}</span>
                  <span v-else-if="p.slo_met === false" class="badge badge-error">{{ t('run.sloMissed') }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
    <p v-if="last" class="small muted">
      {{ t('dashboard.lastRun') }}
      <RouterLink :to="`/runs/${last.id}`">{{ fmt.dateTime(last.finished_at) }}</RouterLink>
      <StatusBadge kind="run" :status="last.status" />
    </p>
  </div>
</template>

<style scoped>
.site-select {
  width: auto;
  max-width: 220px;
}

.refresh-select {
  width: auto;
}

.alert-banner {
  align-items: flex-start;
}

.alert-banner :deep(.icon) {
  color: var(--error);
  margin-top: 2px;
}

.grow {
  flex: 1;
  gap: 4px;
}

.pad {
  padding: var(--space-3);
}

.sat {
  margin-bottom: var(--space-2);
}

.marks-legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-3);
  align-items: center;
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.swatch {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
}

.swatch.deploy {
  background: var(--chart-annotation-deploy);
}

.swatch.comment {
  background: var(--chart-annotation-comment);
}

.swatch.regression {
  background: var(--chart-annotation-regression);
  transform: rotate(45deg) scale(0.8);
}

.swatch.saturated {
  width: 0;
  height: 0;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-bottom: 9px solid var(--warning);
  border-radius: 0;
}

.annotation-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 2px var(--space-3);
  flex-basis: 100%;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
