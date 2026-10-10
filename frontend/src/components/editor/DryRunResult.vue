<script setup lang="ts">
// Результат dry-run: по шагу — статус, тайминги, извлечённые переменные, ошибка; пропущенные шаги.

import { useI18n } from 'vue-i18n'
import type { DryRunOut } from '@/api/types'
import { useErrorText } from '@/composables/useErrorText'
import { useFormat } from '@/composables/useFormat'
import { tk } from '@/i18n'
import Icon from '@/components/Icon.vue'

defineProps<{ result: DryRunOut }>()
const { t } = useI18n()
const fmt = useFormat()
const { codeText } = useErrorText()

const TIMINGS = [
  { key: 'dns_ms', label: tk('charts.timing.dns') },
  { key: 'connect_ms', label: tk('charts.timing.connect') },
  { key: 'tls_ms', label: tk('charts.timing.tls') },
  { key: 'ttfb_ms', label: tk('charts.timing.ttfb') },
  { key: 'transfer_ms', label: tk('charts.timing.transfer') },
] as const

function timing(timings: Record<string, number>, key: string): number | undefined {
  return timings[key] ?? timings[key.replace(/_ms$/, '')]
}
</script>

<template>
  <section class="dry" :aria-label="t('dryRun.title')">
    <div class="callout" :class="result.ok ? 'callout-success' : 'callout-error'" role="status">
      <Icon :name="result.ok ? 'check' : 'warning'" />
      <span>
        {{ result.ok ? t('dryRun.ok') : t('dryRun.failed') }}
        · {{ t('dryRun.total', { value: fmt.ms(result.duration_ms) }) }}
        <template v-if="result.error"> · {{ codeText(result.error) }}</template>
      </span>
    </div>
    <ol class="steps">
      <li v-for="(step, i) in result.steps" :key="`${i}-${step.name}`" class="step" :class="step.ok ? 'ok' : 'bad'">
        <div class="row-between">
          <strong>{{ step.name }}</strong>
          <span class="row">
            <span class="badge" :class="step.ok ? 'badge-success' : 'badge-error'">
              {{ step.ok ? t('dryRun.stepOk') : t('dryRun.stepFailed') }}
            </span>
            <span v-if="step.status !== null" class="badge badge-neutral mono">{{ t('dryRun.status', { status: step.status }) }}</span>
            <span class="small muted num">{{ fmt.ms(step.duration_ms) }}</span>
          </span>
        </div>
        <p class="mono small muted">{{ step.method }} {{ step.path }}</p>
        <p v-if="step.error" class="small text-error">
          {{ codeText(step.error) }}<template v-if="step.detail">: <span class="mono">{{ step.detail }}</span></template>
        </p>
        <dl class="dl timings">
          <template v-for="tm in TIMINGS" :key="tm.key">
            <dt>{{ t(tm.label) }}</dt>
            <dd class="num">{{ fmt.ms(timing(step.timings, tm.key)) }}</dd>
          </template>
          <dt>{{ t('dryRun.responseSize') }}</dt>
          <dd class="num">
            {{ fmt.bytes(step.response_bytes) }}
            <span v-if="step.truncated" class="badge badge-warning">{{ t('dryRun.truncated') }}</span>
          </dd>
        </dl>
        <div v-if="Object.keys(step.extracted).length" class="extracted">
          <p class="field-label">{{ t('dryRun.extracted') }}</p>
          <dl class="dl">
            <template v-for="(value, name) in step.extracted" :key="name">
              <dt class="mono">{{ name }}</dt>
              <dd class="mono">{{ value }}</dd>
            </template>
          </dl>
        </div>
      </li>
    </ol>
    <div v-if="result.skipped_steps.length" class="callout callout-warning small">
      {{ t('dryRun.skipped', { steps: result.skipped_steps.join(', ') }, result.skipped_steps.length) }}
    </div>
  </section>
</template>

<style scoped>
.dry {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.steps {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.step {
  border: 1px solid var(--border);
  border-left: 3px solid var(--success);
  border-radius: var(--radius);
  padding: var(--space-2) var(--space-3);
  background: var(--surface-2);
}

.step.bad {
  border-left-color: var(--error);
}

.step p {
  margin: 2px 0;
}

.timings {
  grid-template-columns: repeat(auto-fill, minmax(70px, max-content) minmax(70px, 1fr));
  margin-top: var(--space-1);
}

.extracted {
  margin-top: var(--space-2);
}
</style>
