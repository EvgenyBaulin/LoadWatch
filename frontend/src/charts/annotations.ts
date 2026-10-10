// Небольшой плагин Chart.js: вертикальные метки на оси X (деплои, комментарии, регрессии).
// Отдельная зависимость (chartjs-plugin-annotation) не нужна — хватает пары десятков строк.

import type { Chart, ChartType, Plugin } from 'chart.js'

export interface ChartMark {
  /** Значение по оси X: время в мс для временной шкалы. */
  x: number
  color: string
  /** Короткая подпись над меткой (обрезается). */
  label?: string
  dashed?: boolean
  /** Ромбик сверху вместо треугольника — для регрессий. */
  shape?: 'triangle' | 'diamond'
}

export interface MarksPluginOptions {
  marks?: ChartMark[]
  font?: string
}

declare module 'chart.js' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface PluginOptionsByType<TType extends ChartType> {
    lwMarks?: MarksPluginOptions
  }
}

const MAX_LABEL = 18

function shorten(text: string): string {
  return text.length > MAX_LABEL ? `${text.slice(0, MAX_LABEL - 1)}…` : text
}

export const marksPlugin: Plugin = {
  id: 'lwMarks',
  afterDatasetsDraw(chart: Chart, _args, rawOptions) {
    const options = rawOptions as MarksPluginOptions
    const marks = options.marks ?? []
    const scale = chart.scales.x
    if (!marks.length || !scale) return
    const { top, bottom, left, right } = chart.chartArea
    const ctx = chart.ctx
    ctx.save()
    ctx.font = options.font ?? '10px system-ui, sans-serif'
    ctx.textBaseline = 'top'
    let lastLabelEnd = -Infinity
    for (const mark of [...marks].sort((a, b) => a.x - b.x)) {
      const x = scale.getPixelForValue(mark.x)
      if (!Number.isFinite(x) || x < left - 0.5 || x > right + 0.5) continue
      ctx.strokeStyle = mark.color
      ctx.fillStyle = mark.color
      ctx.lineWidth = 1
      ctx.setLineDash(mark.dashed ? [4, 3] : [])
      ctx.beginPath()
      ctx.moveTo(x, top)
      ctx.lineTo(x, bottom)
      ctx.stroke()
      ctx.setLineDash([])
      ctx.beginPath()
      if (mark.shape === 'diamond') {
        ctx.moveTo(x, top - 1)
        ctx.lineTo(x + 4, top + 4)
        ctx.lineTo(x, top + 9)
        ctx.lineTo(x - 4, top + 4)
      } else {
        ctx.moveTo(x - 4, top)
        ctx.lineTo(x + 4, top)
        ctx.lineTo(x, top + 6)
      }
      ctx.closePath()
      ctx.fill()
      // Подписи не налезают друг на друга: лишние пропускаем (полный список — в легенде под графиком).
      if (mark.label) {
        const text = shorten(mark.label)
        const width = ctx.measureText(text).width
        const tx = Math.min(x + 6, right - width)
        if (tx > lastLabelEnd + 4) {
          ctx.fillText(text, tx, top + 2)
          lastLabelEnd = tx + width
        }
      }
    }
    ctx.restore()
  },
}
