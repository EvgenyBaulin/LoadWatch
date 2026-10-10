<script setup lang="ts">
// Карточка сайта: верификация, сценарии, запуск, расписание, CI-триггер, удаление.

import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { scenariosApi, sitesApi } from '@/api/endpoints'
import type { SiteOut } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import ErrorMessage from '@/components/ErrorMessage.vue'
import Icon from '@/components/Icon.vue'
import Panel from '@/components/Panel.vue'
import Skeleton from '@/components/Skeleton.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import RunNowPanel from '@/components/site/RunNowPanel.vue'
import SchedulePanel from '@/components/site/SchedulePanel.vue'
import TriggerTokenPanel from '@/components/site/TriggerTokenPanel.vue'
import VerificationPanel from '@/components/site/VerificationPanel.vue'
import { useFormat } from '@/composables/useFormat'
import { useResource } from '@/composables/useResource'
import { PROFILE_TYPE_KEYS } from '@/i18n/labels'
import type { LoadProfileType } from '@/api/types'
import { useToastStore } from '@/stores/toasts'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const fmt = useFormat()
const toasts = useToastStore()
const siteId = String(route.params.siteId)

const site = useResource(() => sitesApi.get(siteId))
const scenarios = useResource(() => scenariosApi.list(siteId))
const scenarioList = computed(() => scenarios.data.value?.items ?? [])

const deleting = ref(false)
const deleteError = ref<unknown>(null)

function onVerified(updated: SiteOut): void {
  site.data.value = updated
}

function profileLabel(type: unknown): string {
  const key = PROFILE_TYPE_KEYS[type as LoadProfileType]
  return key ? t(key) : String(type)
}

async function removeSite(): Promise<void> {
  if (!site.data.value || !window.confirm(t('site.deleteConfirm', { name: site.data.value.name }))) return
  deleting.value = true
  deleteError.value = null
  try {
    await sitesApi.remove(siteId)
    toasts.success(t('site.deleted'))
    await router.push('/sites')
  } catch (err) {
    deleteError.value = err
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div class="page page-narrow">
    <ErrorMessage v-if="site.error.value" :error="site.error.value" retry @retry="site.reload()" />
    <Skeleton v-else-if="!site.data.value" :height="40" />
    <template v-else>
      <header class="page-header">
        <div>
          <div class="row">
            <h2 class="site-title">{{ site.data.value.name }}</h2>
            <StatusBadge kind="site" :status="site.data.value.verification_status" />
          </div>
          <p class="muted small mono">{{ site.data.value.url }}</p>
        </div>
        <div class="row">
          <RouterLink :to="`/sites/${siteId}/dashboard`" class="btn">
            <Icon name="dashboard" :size="14" />{{ t('nav.dashboard') }}
          </RouterLink>
          <RouterLink :to="`/sites/${siteId}/runs`" class="btn">
            <Icon name="runs" :size="14" />{{ t('nav.runs') }}
          </RouterLink>
        </div>
      </header>

      <VerificationPanel :site="site.data.value" @verified="onVerified" />

      <Panel :title="t('site.scenariosTitle')" flush>
        <template #actions>
          <RouterLink :to="`/sites/${siteId}/scenarios/new`" class="btn btn-sm btn-primary">
            <Icon name="plus" :size="12" />{{ t('site.newScenario') }}
          </RouterLink>
        </template>
        <div v-if="scenarios.loading.value" class="pad"><Skeleton :lines="3" /></div>
        <div v-else-if="scenarios.error.value" class="pad">
          <ErrorMessage :error="scenarios.error.value" retry @retry="scenarios.reload()" />
        </div>
        <EmptyState v-else-if="!scenarioList.length" :title="t('site.noScenariosTitle')" :text="t('site.noScenariosText')" compact>
          <RouterLink :to="`/sites/${siteId}/scenarios/new`" class="btn btn-primary">{{ t('site.newScenario') }}</RouterLink>
        </EmptyState>
        <div v-else class="table-wrap">
          <table class="table">
            <caption class="sr-only">{{ t('site.scenariosTitle') }}</caption>
            <thead>
              <tr>
                <th scope="col">{{ t('site.scenarioName') }}</th>
                <th scope="col">{{ t('site.scenarioProfile') }}</th>
                <th scope="col" class="num">{{ t('site.scenarioSteps') }}</th>
                <th scope="col">{{ t('site.scenarioSlo') }}</th>
                <th scope="col">{{ t('site.scenarioUpdated') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in scenarioList" :key="s.id">
                <td>
                  <RouterLink :to="`/scenarios/${s.id}/edit`">{{ s.name }}</RouterLink>
                  <span v-if="s.missing_secrets.length" class="badge badge-warning secret-badge">
                    {{ t('site.missingSecrets', { n: s.missing_secrets.length }, s.missing_secrets.length) }}
                  </span>
                </td>
                <td>{{ profileLabel(s.load_profile.type) }}</td>
                <td class="num">{{ s.flow.length }}</td>
                <td class="small">
                  <template v-if="s.slo_p95_ms !== null || s.slo_max_error_rate !== null">
                    <span v-if="s.slo_p95_ms !== null">{{ t('site.sloP95', { value: fmt.ms(s.slo_p95_ms) }) }}</span>
                    <span v-if="s.slo_max_error_rate !== null"> · {{ t('site.sloErrors', { value: fmt.percent(s.slo_max_error_rate) }) }}</span>
                  </template>
                  <span v-else class="muted">{{ t('site.noSlo') }}</span>
                </td>
                <td class="small muted">{{ fmt.relative(s.updated_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </Panel>

      <RunNowPanel :site="site.data.value" :scenarios="scenarioList" />
      <SchedulePanel :site-id="siteId" :scenarios="scenarioList" />
      <TriggerTokenPanel :site-id="siteId" />

      <Panel :title="t('site.dangerTitle')">
        <div class="row-between">
          <p class="small muted">{{ t('site.deleteText') }}</p>
          <button type="button" class="btn btn-danger" :disabled="deleting" @click="removeSite">
            <Icon name="trash" :size="13" />{{ t('site.delete') }}
          </button>
        </div>
        <ErrorMessage v-if="deleteError" :error="deleteError" />
      </Panel>
    </template>
  </div>
</template>

<style scoped>
.site-title {
  font-size: var(--fs-xl);
}

.pad {
  padding: var(--space-3);
}

.secret-badge {
  margin-left: var(--space-2);
}
</style>
