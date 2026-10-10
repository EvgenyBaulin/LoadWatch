<script setup lang="ts">
// Расписание запусков: cron, часовой пояс, сценарий; показывает ближайшие запуски.

import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { sitesApi } from '@/api/endpoints'
import type { ScenarioOut, ScheduleOut } from '@/api/types'
import ErrorMessage from '@/components/ErrorMessage.vue'
import Panel from '@/components/Panel.vue'
import Skeleton from '@/components/Skeleton.vue'
import { useErrorText } from '@/composables/useErrorText'
import { useFormat } from '@/composables/useFormat'
import { useResource } from '@/composables/useResource'
import { useToastStore } from '@/stores/toasts'

const props = defineProps<{ siteId: string; scenarios: ScenarioOut[] }>()
const { t } = useI18n()
const fmt = useFormat()
const toasts = useToastStore()
const { fieldErrors } = useErrorText()

const CRON_PRESETS = ['0 * * * *', '0 3 * * *', '*/30 * * * *', '0 9 * * 1']

const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone
const timeZones = computed(() => {
  const list = Intl.supportedValuesOf('timeZone')
  return list.includes('UTC') ? list : ['UTC', ...list]
})

const schedule = useResource(() => sitesApi.schedule(props.siteId))
const form = reactive({ cron: '0 3 * * *', timezone: browserZone, enabled: true, scenarioId: '' })
const saved = ref<ScheduleOut | null>(null)
const busy = ref(false)
const error = ref<unknown>(null)
const fields = computed(() => fieldErrors(error.value))

watch(schedule.data, (value) => {
  if (!value) return
  saved.value = value
  form.cron = value.cron
  form.timezone = value.timezone
  form.enabled = value.enabled
  form.scenarioId = value.scenario_id ?? ''
})

watch(
  () => props.scenarios,
  (list) => {
    if (!form.scenarioId) form.scenarioId = list[0]?.id ?? ''
  },
  { immediate: true },
)

async function save(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    saved.value = await sitesApi.saveSchedule(props.siteId, {
      scenario_id: form.scenarioId,
      cron: form.cron.trim(),
      timezone: form.timezone,
      enabled: form.enabled,
    })
    toasts.success(t('schedule.saved'))
  } catch (err) {
    error.value = err
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Panel :title="t('schedule.title')">
    <Skeleton v-if="schedule.loading.value" :lines="3" />
    <ErrorMessage v-else-if="schedule.error.value" :error="schedule.error.value" retry @retry="schedule.reload()" />
    <p v-else-if="!scenarios.length" class="muted small">{{ t('schedule.noScenarios') }}</p>
    <form v-else class="stack" @submit.prevent="save">
      <div class="form-grid">
        <label class="field">
          <span class="field-label">{{ t('schedule.cron') }}</span>
          <input
            v-model="form.cron"
            class="input mono"
            required
            minlength="9"
            maxlength="100"
            :aria-invalid="Boolean(fields.cron)"
            aria-describedby="cron-hint"
          />
          <span v-if="fields.cron" class="field-error">{{ fields.cron.text }}</span>
          <span id="cron-hint" class="field-hint">{{ t('schedule.cronHint') }}</span>
        </label>
        <label class="field">
          <span class="field-label">{{ t('schedule.timezone') }}</span>
          <select v-model="form.timezone" class="select">
            <option v-for="zone in timeZones" :key="zone" :value="zone">{{ zone }}</option>
          </select>
        </label>
        <label class="field">
          <span class="field-label">{{ t('schedule.scenario') }}</span>
          <select v-model="form.scenarioId" class="select" required>
            <option v-for="s in scenarios" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
        </label>
      </div>
      <div class="row">
        <span class="field-hint">{{ t('schedule.presets') }}</span>
        <button v-for="preset in CRON_PRESETS" :key="preset" type="button" class="btn btn-sm mono" @click="form.cron = preset">
          {{ preset }}
        </button>
      </div>
      <div class="row-between">
        <label class="checkbox">
          <input v-model="form.enabled" type="checkbox" />
          {{ t('schedule.enabled') }}
        </label>
        <button type="submit" class="btn btn-primary" :disabled="busy || !form.scenarioId">
          {{ busy ? t('common.saving') : t('common.save') }}
        </button>
      </div>
      <ErrorMessage v-if="error" :error="error" />
      <div v-if="saved" class="upcoming">
        <h3>{{ t('schedule.upcoming') }}</h3>
        <ul v-if="saved.enabled && saved.upcoming.length" class="small num">
          <li v-for="at in saved.upcoming" :key="at">{{ fmt.dateTime(at) }} <span class="muted">({{ fmt.relative(at) }})</span></li>
        </ul>
        <p v-else class="muted small">{{ t('schedule.disabled') }}</p>
        <p v-if="saved.last_run_at" class="muted small">{{ t('schedule.lastRun', { date: fmt.dateTime(saved.last_run_at) }) }}</p>
      </div>
    </form>
  </Panel>
</template>

<style scoped>
.upcoming ul {
  margin: var(--space-1) 0 0;
  padding-left: var(--space-4);
}
</style>
