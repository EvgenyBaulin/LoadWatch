<script setup lang="ts">
// Выбор временного диапазона: готовые интервалы и свой «с … по …».

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RANGE_PRESETS, type RangeKey, type RangePreset } from '@/composables/useTimeRange'
import { tk } from '@/i18n'
import Icon from './Icon.vue'

const props = defineProps<{ rangeKey: RangeKey; from: Date; to: Date }>()
const emit = defineEmits<{ preset: [key: RangePreset]; custom: [from: Date, to: Date] }>()
const { t } = useI18n()

const PRESET_KEYS: Record<RangePreset, string> = {
  '1h': tk('timeRange.1h'),
  '6h': tk('timeRange.6h'),
  '24h': tk('timeRange.24h'),
  '7d': tk('timeRange.7d'),
  '30d': tk('timeRange.30d'),
}

const open = ref(false)
const fromInput = ref('')
const toInput = ref('')
const invalid = ref(false)

/** Date → значение для <input type="datetime-local"> в часовом поясе браузера. */
function toLocalInput(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

watch(open, (value) => {
  if (value) {
    fromInput.value = toLocalInput(props.from)
    toInput.value = toLocalInput(props.to)
    invalid.value = false
  }
})

const label = computed(() =>
  props.rangeKey === 'custom' ? t('timeRange.custom') : t(PRESET_KEYS[props.rangeKey]),
)

function apply(): void {
  const from = new Date(fromInput.value)
  const to = new Date(toInput.value)
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime()) || from >= to) {
    invalid.value = true
    return
  }
  emit('custom', from, to)
  open.value = false
}

function pick(key: RangePreset): void {
  emit('preset', key)
  open.value = false
}
</script>

<template>
  <div class="picker" @keydown.esc="open = false">
    <button
      type="button"
      class="btn"
      :aria-expanded="open"
      aria-haspopup="dialog"
      :aria-label="t('timeRange.label', { range: label })"
      @click="open = !open"
    >
      <Icon name="clock" :size="14" />
      {{ label }}
      <Icon name="chevronDown" :size="12" />
    </button>
    <div v-if="open" class="popover" role="dialog" :aria-label="t('timeRange.title')">
      <div class="presets">
        <button
          v-for="key in RANGE_PRESETS"
          :key="key"
          type="button"
          class="btn btn-sm"
          :class="{ 'btn-primary': rangeKey === key }"
          :aria-pressed="rangeKey === key"
          @click="pick(key)"
        >
          {{ t(PRESET_KEYS[key]) }}
        </button>
      </div>
      <form class="custom" @submit.prevent="apply">
        <p class="field-label">{{ t('timeRange.custom') }}</p>
        <label class="field">
          <span class="field-hint">{{ t('timeRange.from') }}</span>
          <input v-model="fromInput" class="input" type="datetime-local" :aria-invalid="invalid" />
        </label>
        <label class="field">
          <span class="field-hint">{{ t('timeRange.to') }}</span>
          <input v-model="toInput" class="input" type="datetime-local" :aria-invalid="invalid" />
        </label>
        <p v-if="invalid" class="field-error">{{ t('timeRange.invalid') }}</p>
        <button type="submit" class="btn btn-primary btn-sm">{{ t('timeRange.apply') }}</button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.picker {
  position: relative;
}

.popover {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: 40;
  width: 260px;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.presets {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}

.custom {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  border-top: 1px solid var(--border);
  padding-top: var(--space-2);
}

.custom .field-label {
  margin: 0;
}
</style>
