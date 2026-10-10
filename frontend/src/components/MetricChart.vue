<script setup lang="ts">
// Универсальный график (линии и столбцы) поверх vue-chartjs.
// Цвета берутся из токенов темы, подписи — от вызывающего (уже переведённые).

import { computed } from 'vue'
import { Chart as ChartView } from 'vue-chartjs'
import type { ChartData, ChartDataset, ChartOptions, TooltipItem } from 'chart.js'
import { enUS, ru } from 'date-fns/locale'
import { useI18n } from 'vue-i18n'
import '@/charts/setup'
import type { ChartMark } from '@/charts/annotations'
import { useChartTheme, withAlpha } from '@/charts/useChartTheme'
import { useFormat } from '@/composables/useFormat'

export interface ChartPoint {
  x: number
  y: number | null
}

export interface SeriesDef {
  label: string
  data: ChartPoint[] | (number | null)[]
  color: string
  type?: 'line' | 'bar'
  axis?: 'y' | 'y2'
  stack?: string
  dashed?: boolean
  fill?: boolean
  /** Цвета отдельных точек/столбцов (например, предупреждение у насыщенных запусков). */
  pointColors?: string[]
  pointRadius?: number[] | number
  pointStyles?: ('circle' | 'triangle' | 'rectRot' | 'crossRot')[]
  order?: number
}

const props = withDefaults(
  defineProps<{
    series: SeriesDef[]
    kind?: 'line' | 'bar'
    xType?: 'time' | 'linear' | 'category'
    labels?: string[]
    xMin?: number
    xMax?: number
    xTitle?: string
    yTitle?: string
    y2Title?: string
    yFormat?: (value: number) => string
    y2Format?: (value: number) => string
    xFormat?: (value: number) => string
    stacked?: boolean
    marks?: ChartMark[]
    height?: number
    label: string
    yMin?: number
    y2Max?: number
  }>(),
  {
    kind: 'line',
    xType: 'time',
    labels: undefined,
    xMin: undefined,
    xMax: undefined,
    xTitle: undefined,
    yTitle: undefined,
    y2Title: undefined,
    yFormat: undefined,
    y2Format: undefined,
    xFormat: undefined,
    stacked: false,
    marks: () => [],
    height: 220,
    yMin: 0,
    y2Max: undefined,
  },
)

const palette = useChartTheme()
const { locale } = useI18n()
const fmt = useFormat()

const hasY2 = computed(() => props.series.some((s) => s.axis === 'y2'))

const chartData = computed<ChartData<'line' | 'bar'>>(() => ({
  labels: props.xType === 'category' ? props.labels : undefined,
  datasets: props.series.map((s) => {
    const type = s.type ?? props.kind
    const base = {
      type,
      label: s.label,
      data: s.data,
      yAxisID: s.axis ?? 'y',
      borderColor: s.color,
      backgroundColor: type === 'bar' ? withAlpha(s.color, 0.75) : withAlpha(s.color, 0.12),
      order: s.order ?? (type === 'line' ? 0 : 1),
      stack: s.stack,
    }
    if (type === 'bar') {
      return {
        ...base,
        borderWidth: 0,
        backgroundColor: s.pointColors ?? base.backgroundColor,
        maxBarThickness: 28,
      } as ChartDataset<'bar'>
    }
    return {
      ...base,
      borderWidth: 1.5,
      borderDash: s.dashed ? [5, 4] : undefined,
      fill: s.fill ?? false,
      tension: 0,
      spanGaps: true,
      pointRadius: s.pointRadius ?? (s.dashed ? 0 : 2),
      pointHoverRadius: 4,
      pointBackgroundColor: s.pointColors ?? s.color,
      pointBorderColor: s.pointColors ?? s.color,
      pointStyle: s.pointStyles ?? 'circle',
    } as ChartDataset<'line'>
  }) as ChartDataset<'line' | 'bar'>[],
}))

function tickFormatter(format?: (value: number) => string) {
  return (value: number | string) => (format ? format(Number(value)) : fmt.num(Number(value)))
}

const chartOptions = computed<ChartOptions<'line' | 'bar'>>(() => {
  const p = palette.value
  const font = { family: p.fontFamily, size: 11 }
  const dateLocale = locale.value === 'ru' ? ru : enUS
  const grid = { color: p.grid, drawTicks: false }
  const border = { color: p.grid }
  const x =
    props.xType === 'time'
      ? {
          type: 'time' as const,
          min: props.xMin,
          max: props.xMax,
          adapters: { date: { locale: dateLocale } },
          // 24-часовой формат: в русской локали date-fns иначе пишет «11:43 ПП».
          time: {
            tooltipFormat: 'd MMM HH:mm:ss',
            displayFormats: {
              second: 'HH:mm:ss',
              minute: 'HH:mm',
              hour: 'HH:mm',
              day: 'd MMM',
              week: 'd MMM',
              month: 'MMM yyyy',
            },
          },
          ticks: { color: p.muted, font, maxRotation: 0, autoSkipPadding: 16 },
          grid,
          border,
          stacked: props.stacked,
          offset: props.kind === 'bar',
        }
      : {
          type: props.xType,
          ticks: {
            color: p.muted,
            font,
            maxRotation: 0,
            autoSkipPadding: 12,
            callback: props.xFormat
              ? (value: number | string) => props.xFormat?.(Number(value)) ?? String(value)
              : undefined,
          },
          title: props.xTitle ? { display: true, text: props.xTitle, color: p.muted, font } : undefined,
          grid,
          border,
          stacked: props.stacked,
        }
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    layout: { padding: { top: props.marks.length ? 14 : 4 } },
    scales: {
      x,
      y: {
        type: 'linear',
        min: props.yMin,
        stacked: props.stacked,
        ticks: { color: p.muted, font, padding: 6, callback: tickFormatter(props.yFormat), maxTicksLimit: 6 },
        title: props.yTitle ? { display: true, text: props.yTitle, color: p.muted, font } : undefined,
        grid,
        border,
      },
      ...(hasY2.value
        ? {
            y2: {
              type: 'linear' as const,
              position: 'right' as const,
              min: 0,
              max: props.y2Max,
              ticks: { color: p.muted, font, padding: 6, callback: tickFormatter(props.y2Format), maxTicksLimit: 6 },
              title: props.y2Title ? { display: true, text: props.y2Title, color: p.muted, font } : undefined,
              grid: { drawOnChartArea: false, drawTicks: false },
              border,
            },
          }
        : {}),
    },
    plugins: {
      legend: {
        position: 'bottom',
        align: 'start',
        labels: { color: p.text, font, boxWidth: 10, boxHeight: 10, padding: 10 },
      },
      tooltip: {
        backgroundColor: p.tooltipBg,
        titleColor: p.tooltipText,
        bodyColor: p.tooltipText,
        borderColor: p.grid,
        borderWidth: 1,
        titleFont: font,
        bodyFont: font,
        padding: 8,
        callbacks: {
          title: (items: TooltipItem<'line' | 'bar'>[]) => {
            const first = items[0]
            if (!first) return ''
            if (props.xType === 'time') return fmt.dateTimeShort(first.parsed.x ?? 0)
            if (props.xType === 'linear' && props.xFormat) return props.xFormat(first.parsed.x ?? 0)
            return first.label
          },
          label: (item: TooltipItem<'line' | 'bar'>) => {
            const value = item.parsed.y
            if (value === null || value === undefined) return `${item.dataset.label}: ${fmt.dash.value}`
            const axisFormat = item.dataset.yAxisID === 'y2' ? props.y2Format : props.yFormat
            return `${item.dataset.label}: ${axisFormat ? axisFormat(value) : fmt.num(value)}`
          },
        },
      },
      lwMarks: { marks: props.marks, font: `10px ${p.fontFamily}` },
    },
  } as ChartOptions<'line' | 'bar'>
})
</script>

<template>
  <div class="chart" :style="{ height: `${height}px` }">
    <ChartView
      :type="kind"
      :data="chartData as never"
      :options="chartOptions as never"
      role="img"
      :aria-label="label"
    />
  </div>
</template>

<style scoped>
.chart {
  position: relative;
  width: 100%;
  min-width: 0;
}
</style>
