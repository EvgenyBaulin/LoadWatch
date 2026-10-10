// Цвета графиков из CSS-переменных текущей темы. Пересчитываются при смене темы,
// поэтому Chart.js перекрашивается без перезагрузки страницы.

import { computed } from 'vue'
import { usePreferencesStore } from '@/stores/preferences'

export interface ChartPalette {
  text: string
  muted: string
  grid: string
  tooltipBg: string
  tooltipText: string
  series: string[]
  success: string
  warning: string
  error: string
  info: string
  slo: string
  deploy: string
  comment: string
  regression: string
  fontFamily: string
}

function cssVar(styles: CSSStyleDeclaration, name: string): string {
  return styles.getPropertyValue(name).trim()
}

export function readPalette(): ChartPalette {
  const styles = getComputedStyle(document.documentElement)
  const v = (name: string) => cssVar(styles, name)
  return {
    text: v('--text'),
    muted: v('--chart-axis'),
    grid: v('--chart-grid'),
    tooltipBg: v('--chart-tooltip-bg'),
    tooltipText: v('--chart-tooltip-text'),
    series: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => v(`--series-${i}`)),
    success: v('--success'),
    warning: v('--warning'),
    error: v('--error'),
    info: v('--info'),
    slo: v('--chart-slo'),
    deploy: v('--chart-annotation-deploy'),
    comment: v('--chart-annotation-comment'),
    regression: v('--chart-annotation-regression'),
    fontFamily: v('--font-sans'),
  }
}

/** Реактивная палитра: зависит от resolvedTheme, значит обновляется при её смене. */
export function useChartTheme() {
  const prefs = usePreferencesStore()
  return computed<ChartPalette>(() => {
    void prefs.resolvedTheme
    return readPalette()
  })
}

/** Цвет с прозрачностью для заливок: работает с #rrggbb и rgba(). */
export function withAlpha(color: string, alpha: number): string {
  const hex = /^#([0-9a-f]{6})$/i.exec(color)
  if (hex?.[1]) {
    const n = parseInt(hex[1], 16)
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
  }
  return color
}
