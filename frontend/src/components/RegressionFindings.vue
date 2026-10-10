<script setup lang="ts">
// Найденные регрессии: текст собирается из ключа перевода и чисел (метрика, база, текущее, %).

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Regression, RegressionFinding, RegressionMetric } from '@/api/types'
import { useFormat } from '@/composables/useFormat'
import { REGRESSION_METRIC_KEYS } from '@/i18n/labels'
import Icon from './Icon.vue'

const props = withDefaults(defineProps<{ regression: Regression | null | undefined; compact?: boolean }>(), {
  compact: false,
})
const { t } = useI18n()
const fmt = useFormat()

const findings = computed<RegressionFinding[]>(() => props.regression?.findings ?? [])

function value(metric: RegressionMetric, v: number): string {
  switch (metric) {
    case 'p95_ms':
      return fmt.ms(v)
    case 'error_rate':
      return fmt.percent(v)
    case 'apdex':
      return fmt.num(v, 2)
    case 'ceiling_rps':
      return fmt.rps(v)
  }
}

function text(f: RegressionFinding): string {
  const key = REGRESSION_METRIC_KEYS[f.metric]
  if (!key) return f.message_key
  return t(
    key,
    {
      baseline: value(f.metric, f.baseline),
      current: value(f.metric, f.current),
      pct: f.change_pct === null ? fmt.dash.value : fmt.num(Math.abs(f.change_pct), 1),
      n: f.baseline_runs,
    },
    f.baseline_runs,
  )
}
</script>

<template>
  <div v-if="findings.length" class="findings" :class="{ compact }">
    <p v-if="!compact" class="title"><Icon name="warning" :size="14" /> {{ t('regression.title') }}</p>
    <ul>
      <li v-for="f in findings" :key="f.metric">{{ text(f) }}</li>
    </ul>
  </div>
</template>

<style scoped>
.findings {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--fs-sm);
}

.title {
  margin: 0;
  font-weight: 600;
  color: var(--error);
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

ul {
  margin: 0;
  padding-left: var(--space-4);
}

.compact ul {
  padding-left: var(--space-3);
}
</style>
