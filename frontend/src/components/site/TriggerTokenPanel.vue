<script setup lang="ts">
// Токен CI-триггера: создаётся заново по кнопке и показывается один раз.

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { sitesApi } from '@/api/endpoints'
import type { TriggerTokenOut } from '@/api/types'
import CopyButton from '@/components/CopyButton.vue'
import ErrorMessage from '@/components/ErrorMessage.vue'
import Panel from '@/components/Panel.vue'

const props = defineProps<{ siteId: string }>()
const { t } = useI18n()

const result = ref<TriggerTokenOut | null>(null)
const busy = ref(false)
const error = ref<unknown>(null)

async function generate(): Promise<void> {
  if (!window.confirm(t('trigger.confirm'))) return
  busy.value = true
  error.value = null
  try {
    result.value = await sitesApi.triggerToken(props.siteId)
  } catch (err) {
    error.value = err
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Panel :title="t('trigger.title')">
    <div class="stack">
      <p class="small muted">{{ t('trigger.intro') }}</p>
      <template v-if="result">
        <p class="callout callout-warning small" role="status">{{ t('trigger.showOnce') }}</p>
        <div class="field">
          <span class="field-label">{{ t('trigger.token') }}</span>
          <div class="row"><code class="token">{{ result.token }}</code><CopyButton :text="result.token" /></div>
        </div>
        <div class="field">
          <div class="row-between">
            <span class="field-label">{{ t('trigger.example') }}</span>
            <CopyButton :text="result.example" />
          </div>
          <pre>{{ result.example }}</pre>
        </div>
      </template>
      <div>
        <button type="button" class="btn" :disabled="busy" @click="generate">
          {{ busy ? t('common.loading') : t('trigger.generate') }}
        </button>
      </div>
      <ErrorMessage v-if="error" :error="error" />
    </div>
  </Panel>
</template>

<style scoped>
.token {
  max-width: 100%;
}
</style>
