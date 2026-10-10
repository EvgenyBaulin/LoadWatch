// Форматирование чисел, единиц и дат через Intl с учётом текущего языка.
// Даты показываются в часовом поясе браузера (API отдаёт UTC).

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

type Num = number | null | undefined

export function useFormat() {
  const { locale, t } = useI18n()

  const cache = computed(() => {
    const loc = locale.value
    return {
      int: new Intl.NumberFormat(loc, { maximumFractionDigits: 0 }),
      dec1: new Intl.NumberFormat(loc, { maximumFractionDigits: 1 }),
      dec2: new Intl.NumberFormat(loc, { maximumFractionDigits: 2 }),
      pct: new Intl.NumberFormat(loc, { style: 'percent', maximumFractionDigits: 2 }),
      pct1: new Intl.NumberFormat(loc, { style: 'percent', maximumFractionDigits: 1 }),
      ms: new Intl.NumberFormat(loc, {
        style: 'unit',
        unit: 'millisecond',
        unitDisplay: 'short',
        maximumFractionDigits: 1,
      }),
      sec: new Intl.NumberFormat(loc, {
        style: 'unit',
        unit: 'second',
        unitDisplay: 'short',
        maximumFractionDigits: 1,
      }),
      min: new Intl.NumberFormat(loc, {
        style: 'unit',
        unit: 'minute',
        unitDisplay: 'short',
        maximumFractionDigits: 1,
      }),
      bytes: (['byte', 'kilobyte', 'megabyte', 'gigabyte'] as const).map(
        (unit) => new Intl.NumberFormat(loc, { style: 'unit', unit, unitDisplay: 'short', maximumFractionDigits: 1 }),
      ),
      dateTime: new Intl.DateTimeFormat(loc, { dateStyle: 'short', timeStyle: 'medium' }),
      dateTimeShort: new Intl.DateTimeFormat(loc, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      }),
      time: new Intl.DateTimeFormat(loc, { timeStyle: 'medium' }),
      relative: new Intl.RelativeTimeFormat(loc, { numeric: 'auto' }),
    }
  })

  const dash = computed(() => t('common.noValue'))

  function num(value: Num, digits: 0 | 1 | 2 = 2): string {
    if (value === null || value === undefined || Number.isNaN(value)) return dash.value
    const f = digits === 0 ? cache.value.int : digits === 1 ? cache.value.dec1 : cache.value.dec2
    return f.format(value)
  }

  /** Доля 0..1 → проценты. */
  function percent(rate: Num, digits: 1 | 2 = 2): string {
    if (rate === null || rate === undefined || Number.isNaN(rate)) return dash.value
    return (digits === 1 ? cache.value.pct1 : cache.value.pct).format(rate)
  }

  /** Уже готовое значение в процентах (например, 87.5) → «87,5 %». */
  function percentValue(value: Num): string {
    return value === null || value === undefined ? dash.value : percent(value / 100, 1)
  }

  function ms(value: Num): string {
    if (value === null || value === undefined || Number.isNaN(value)) return dash.value
    return cache.value.ms.format(value)
  }

  function rps(value: Num): string {
    if (value === null || value === undefined || Number.isNaN(value)) return dash.value
    return t('units.rps', { value: num(value, value >= 100 ? 0 : 1) })
  }

  function seconds(value: Num): string {
    if (value === null || value === undefined || Number.isNaN(value)) return dash.value
    if (value >= 120) return cache.value.min.format(value / 60)
    return cache.value.sec.format(value)
  }

  function bytes(value: Num): string {
    if (value === null || value === undefined || Number.isNaN(value)) return dash.value
    // Единицы по 1024: Б, кБ, МБ, ГБ — подписи даёт Intl на текущем языке.
    let index = 0
    let scaled = value
    while (scaled >= 1024 && index < 3) {
      scaled /= 1024
      index++
    }
    return cache.value.bytes[index]?.format(scaled) ?? String(value)
  }

  function dateTime(iso: string | null | undefined): string {
    if (!iso) return dash.value
    return cache.value.dateTime.format(new Date(iso))
  }

  function dateTimeShort(value: string | number | Date | null | undefined): string {
    if (value === null || value === undefined || value === '') return dash.value
    return cache.value.dateTimeShort.format(new Date(value))
  }

  /** «5 минут назад» / «через 2 часа». */
  function relative(iso: string | null | undefined): string {
    if (!iso) return dash.value
    const diffSec = (new Date(iso).getTime() - Date.now()) / 1000
    const abs = Math.abs(diffSec)
    const rel = cache.value.relative
    if (abs < 60) return rel.format(Math.round(diffSec), 'second')
    if (abs < 3600) return rel.format(Math.round(diffSec / 60), 'minute')
    if (abs < 86400) return rel.format(Math.round(diffSec / 3600), 'hour')
    return rel.format(Math.round(diffSec / 86400), 'day')
  }

  /** Длительность запуска между двумя отметками времени. */
  function duration(fromIso: string | null | undefined, toIso: string | null | undefined): string {
    if (!fromIso || !toIso) return dash.value
    return seconds((new Date(toIso).getTime() - new Date(fromIso).getTime()) / 1000)
  }

  return { num, percent, percentValue, ms, rps, seconds, bytes, dateTime, dateTimeShort, relative, duration, dash }
}
