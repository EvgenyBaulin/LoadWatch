// Временной диапазон и автообновление дашборда, хранятся в query-параметрах URL
// (ссылкой можно поделиться, «назад» в браузере работает).

import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

export const RANGE_PRESETS = ['1h', '6h', '24h', '7d', '30d'] as const
export type RangePreset = (typeof RANGE_PRESETS)[number]
export type RangeKey = RangePreset | 'custom'

export const REFRESH_OPTIONS = ['off', '30s', '1m', '5m'] as const
export type RefreshKey = (typeof REFRESH_OPTIONS)[number]

const PRESET_MS: Record<RangePreset, number> = {
  '1h': 3600_000,
  '6h': 6 * 3600_000,
  '24h': 24 * 3600_000,
  '7d': 7 * 86400_000,
  '30d': 30 * 86400_000,
}

export const REFRESH_MS: Record<RefreshKey, number> = {
  off: 0,
  '30s': 30_000,
  '1m': 60_000,
  '5m': 300_000,
}

const DEFAULT_RANGE: RangePreset = '7d'

function parseDate(value: unknown): Date | null {
  if (typeof value !== 'string' || !value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

export function useTimeRange() {
  const route = useRoute()
  const router = useRouter()
  // «Сейчас» фиксируется при каждой загрузке, чтобы все запросы шли за один и тот же период.
  const now = ref(Date.now())

  const rangeKey = computed<RangeKey>(() => {
    const value = route.query.range
    if (value === 'custom' && parseDate(route.query.from) && parseDate(route.query.to)) return 'custom'
    return RANGE_PRESETS.includes(value as RangePreset) ? (value as RangePreset) : DEFAULT_RANGE
  })

  const refreshKey = computed<RefreshKey>(() => {
    const value = route.query.refresh
    return REFRESH_OPTIONS.includes(value as RefreshKey) ? (value as RefreshKey) : 'off'
  })

  const range = computed(() => {
    if (rangeKey.value === 'custom') {
      return {
        from: parseDate(route.query.from) as Date,
        to: parseDate(route.query.to) as Date,
      }
    }
    return { from: new Date(now.value - PRESET_MS[rangeKey.value]), to: new Date(now.value) }
  })

  /** Число дней для сводки (доля запусков в SLO) — по ширине диапазона, 1…365. */
  const periodDays = computed(() => {
    const ms = range.value.to.getTime() - range.value.from.getTime()
    return Math.min(365, Math.max(1, Math.ceil(ms / 86400_000)))
  })

  function setPreset(key: RangePreset): void {
    void router.replace({ query: { ...route.query, range: key, from: undefined, to: undefined } })
  }

  function setCustom(from: Date, to: Date): void {
    void router.replace({
      query: { ...route.query, range: 'custom', from: from.toISOString(), to: to.toISOString() },
    })
  }

  function setRefresh(key: RefreshKey): void {
    void router.replace({ query: { ...route.query, refresh: key === 'off' ? undefined : key } })
  }

  function touch(): void {
    now.value = Date.now()
  }

  return { rangeKey, refreshKey, range, periodDays, setPreset, setCustom, setRefresh, touch }
}
