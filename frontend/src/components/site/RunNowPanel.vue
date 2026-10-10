<script setup lang="ts">
// Кнопка «Запустить сейчас»: выбор сценария и необязательная метка.

import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { runsApi } from '@/api/endpoints'
import type { ScenarioOut, SiteOut } from '@/api/types'
import ErrorMessage from '@/components/ErrorMessage.vue'
import Icon from '@/components/Icon.vue'
import Panel from '@/components/Panel.vue'

const props = defineProps<{ site: SiteOut; scenarios: ScenarioOut[] }>()
const { t } = useI18n()
const router = useRouter()

const scenarioId = ref('')
const label = ref('')
const busy = ref(false)
const error = ref<unknown>(null)

watch(
  () => props.scenarios,
  (list) => {
    if (!list.some((s) => s.id === scenarioId.value)) scenarioId.value = list[0]?.id ?? ''
  },
  { immediate: true },
)

async function start(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    const run = await runsApi.create(props.site.id, {
      scenario_id: scenarioId.value,
      label: label.value.trim() || null,
    })
    await router.push(`/runs/${run.id}`)
  } catch (err) {
    error.value = err
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Panel :title="t('runNow.title')">
    <p v-if="site.verification_status !== 'verified'" class="callout callout-warning small">
      {{ t('runNow.needsVerification') }}
    </p>
    <p v-else-if="!scenarios.length" class="muted small">{{ t('runNow.noScenarios') }}</p>
    <form v-else class="run-form" @submit.prevent="start">
      <label class="field">
        <span class="field-label">{{ t('runNow.scenario') }}</span>
        <select v-model="scenarioId" class="select">
          <option v-for="s in scenarios" :key="s.id" :value="s.id">{{ s.name }}</option>
        </select>
      </label>
      <label class="field">
        <span class="field-label">{{ t('runNow.label') }}</span>
        <input v-model="label" class="input" maxlength="200" :placeholder="t('runNow.labelPlaceholder')" />
      </label>
      <button type="submit" class="btn btn-primary" :disabled="busy || !scenarioId">
        <Icon name="play" :size="13" />
        {{ busy ? t('runNow.starting') : t('runNow.start') }}
      </button>
    </form>
    <ErrorMessage v-if="error" :error="error" class="mt" />
  </Panel>
</template>

<style scoped>
.run-form {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  gap: var(--space-2);
  align-items: end;
}

@media (max-width: 640px) {
  .run-form {
    grid-template-columns: 1fr;
  }
}

.mt {
  margin-top: var(--space-2);
}
</style>
