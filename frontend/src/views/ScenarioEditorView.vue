<script setup lang="ts">
// Редактор сценария: профиль нагрузки, конструктор потока, SLO/Apdex, секреты, dry-run.

import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { scenariosApi } from '@/api/endpoints'
import type { DryRunOut, LoadProfileType, ScenarioOut } from '@/api/types'
import DryRunResult from '@/components/editor/DryRunResult.vue'
import StepEditor from '@/components/editor/StepEditor.vue'
import ErrorMessage from '@/components/ErrorMessage.vue'
import Icon from '@/components/Icon.vue'
import Panel from '@/components/Panel.vue'
import Skeleton from '@/components/Skeleton.vue'
import { useErrorText } from '@/composables/useErrorText'
import { useFormat } from '@/composables/useFormat'
import { PROFILE_FIELD_KEYS, PROFILE_TYPE_KEYS } from '@/i18n/labels'
import { useToastStore } from '@/stores/toasts'
import {
  emptyScenario,
  jsonError,
  MAX_STEPS,
  newStep,
  scenarioFromApi,
  scenarioToApi,
  type ScenarioForm,
} from '@/utils/scenarioForm'

interface SecretRow {
  name: string
  value: string
  stored: boolean
  remove: boolean
}

const SECRET_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/
const PROFILE_TYPES: LoadProfileType[] = ['constant', 'ramp_up', 'step']
// Пример подстановки показываем как код, а не как переводимую строку.
const TEMPLATE_VAR = '{{ token }}'
const TEMPLATE_SECRET = '{{ secret.API_KEY }}'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const fmt = useFormat()
const toasts = useToastStore()
const { errorText, fieldErrors } = useErrorText()

const scenarioId = ref<string | null>(typeof route.params.scenarioId === 'string' ? route.params.scenarioId : null)
const siteId = ref<string | null>(typeof route.params.siteId === 'string' ? route.params.siteId : null)

const form = reactive<ScenarioForm>(emptyScenario())
const saved = ref<ScenarioOut | null>(null)
const snapshot = ref('')
const secrets = ref<SecretRow[]>([])
const loading = ref(Boolean(scenarioId.value))
const loadError = ref<unknown>(null)
const saving = ref(false)
const saveError = ref<unknown>(null)
const dryRunning = ref(false)
const dryRun = ref<DryRunOut | null>(null)
const dryRunError = ref<unknown>(null)
const localError = ref<string | null>(null)

const errors = computed(() => fieldErrors(saveError.value))

function serialize(): string {
  return JSON.stringify({ form, secrets: secrets.value.filter((s) => s.value || s.remove || !s.stored) })
}

const dirty = computed(() => serialize() !== snapshot.value)

function applyScenario(s: ScenarioOut): void {
  saved.value = s
  siteId.value = s.site_id
  Object.assign(form, scenarioFromApi(s))
  secrets.value = s.secrets.map((sec) => ({ name: sec.name, value: '', stored: true, remove: false }))
  snapshot.value = serialize()
}

onMounted(async () => {
  if (!scenarioId.value) {
    snapshot.value = serialize()
    return
  }
  try {
    applyScenario(await scenariosApi.get(scenarioId.value))
  } catch (err) {
    loadError.value = err
  } finally {
    loading.value = false
  }
})

// Предупреждение о несохранённых изменениях при уходе со страницы.
function beforeUnload(event: BeforeUnloadEvent): void {
  if (dirty.value) event.preventDefault()
}
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
onBeforeRouteLeave(() => (dirty.value && !saving.value ? window.confirm(t('editor.leaveConfirm')) : true))

// ---- поток ----

function addStep(): void {
  if (form.steps.length >= MAX_STEPS) return
  form.steps.push(newStep(form.steps.length))
}

function move(index: number, delta: number): void {
  const target = index + delta
  if (target < 0 || target >= form.steps.length) return
  const [step] = form.steps.splice(index, 1)
  if (step) form.steps.splice(target, 0, step)
}

function removeStep(index: number): void {
  if (form.steps.length > 1) form.steps.splice(index, 1)
}

// ---- секреты ----

function addSecret(name = ''): void {
  if (name && secrets.value.some((s) => s.name === name)) return
  secrets.value.push({ name, value: '', stored: false, remove: false })
}

function removeSecret(index: number): void {
  const row = secrets.value[index]
  if (!row) return
  if (row.stored) row.remove = !row.remove
  else secrets.value.splice(index, 1)
}

const secretNameErrors = computed(() =>
  secrets.value.map((s) => (!s.stored && s.name && !SECRET_NAME_RE.test(s.name) ? t('editor.secrets.invalidName') : null)),
)

const missingSecrets = computed(() => saved.value?.missing_secrets ?? [])

function secretChanges(): Record<string, string | null> {
  const out: Record<string, string | null> = {}
  for (const row of secrets.value) {
    const name = row.name.trim()
    if (!name) continue
    if (row.remove) out[name] = null
    else if (row.value) out[name] = row.value
  }
  return out
}

// ---- сохранение ----

function validateLocally(): string | null {
  if (!form.name.trim()) return t('editor.errors.nameRequired')
  for (const [i, step] of form.steps.entries()) {
    if (step.bodyType === 'json' && jsonError(step.bodyText)) return t('editor.errors.stepJson', { n: i + 1 })
    if (!step.path.trim().startsWith('/')) return t('editor.errors.stepPath', { n: i + 1 })
  }
  const names = form.steps.map((s) => s.name.trim())
  if (new Set(names).size !== names.length) return t('editor.errors.duplicateStepNames')
  if (secretNameErrors.value.some(Boolean)) return t('editor.secrets.invalidName')
  return null
}

async function save(): Promise<boolean> {
  localError.value = validateLocally()
  if (localError.value) return false
  saving.value = true
  saveError.value = null
  try {
    const body = scenarioToApi(form)
    let result = scenarioId.value
      ? await scenariosApi.update(scenarioId.value, body)
      : await scenariosApi.create(siteId.value ?? '', body)
    const changes = secretChanges()
    if (Object.keys(changes).length) {
      result = await scenariosApi.updateSecrets(result.id, changes)
    }
    const created = !scenarioId.value
    scenarioId.value = result.id
    applyScenario(result)
    toasts.success(t('editor.saved'))
    if (created) await router.replace(`/scenarios/${result.id}/edit`)
    return true
  } catch (err) {
    saveError.value = err
    return false
  } finally {
    saving.value = false
  }
}

async function runDry(): Promise<void> {
  // Dry-run выполняет сохранённую версию — несохранённые правки сначала сохраняем.
  if (dirty.value || !scenarioId.value) {
    if (!(await save())) return
  }
  if (!scenarioId.value) return
  dryRunning.value = true
  dryRunError.value = null
  dryRun.value = null
  try {
    dryRun.value = await scenariosApi.dryRun(scenarioId.value)
  } catch (err) {
    dryRunError.value = err
  } finally {
    dryRunning.value = false
  }
}

async function remove(): Promise<void> {
  if (!scenarioId.value || !window.confirm(t('editor.deleteConfirm', { name: form.name }))) return
  try {
    await scenariosApi.remove(scenarioId.value)
    snapshot.value = serialize()
    toasts.success(t('editor.deleted'))
    await router.push(siteId.value ? `/sites/${siteId.value}` : '/sites')
  } catch (err) {
    toasts.error(errorText(err))
  }
}

// ---- эффективный профиль ----

const effective = computed(() => {
  if (!saved.value) return []
  const configured = saved.value.load_profile
  return Object.entries(saved.value.effective_load_profile)
    .filter(([key, value]) => key !== 'type' && value !== null && value !== undefined)
    .map(([key, value]) => ({
      key,
      label: PROFILE_FIELD_KEYS[key] ? t(PROFILE_FIELD_KEYS[key] ?? '') : key,
      value: typeof value === 'number' ? fmt.num(value) : String(value),
      clamped: configured[key] !== undefined && configured[key] !== value,
    }))
})

function profileErr(field: string): string | undefined {
  const e = errors.value[`load_profile.${field}`]
  return e ? e.text : undefined
}
</script>

<template>
  <div class="page page-narrow">
    <ErrorMessage v-if="loadError" :error="loadError" />
    <Skeleton v-else-if="loading" :lines="6" :height="24" />
    <form v-else class="stack" novalidate @submit.prevent="save">
      <header class="editor-head">
        <label class="field name-field">
          <span class="field-label">{{ t('editor.name') }}</span>
          <input
            v-model="form.name"
            class="input name-input"
            maxlength="100"
            required
            :placeholder="t('editor.namePlaceholder')"
            :aria-invalid="Boolean(errors.name)"
          />
          <span v-if="errors.name" class="field-error">{{ errors.name.text }}</span>
        </label>
        <div class="row">
          <span v-if="dirty" class="badge badge-warning">{{ t('editor.unsaved') }}</span>
          <RouterLink v-if="siteId" :to="`/sites/${siteId}`" class="btn">{{ t('common.back') }}</RouterLink>
          <button type="button" class="btn" :disabled="saving || dryRunning" @click="runDry">
            <Icon name="play" :size="13" />{{ dryRunning ? t('dryRun.running') : t('dryRun.button') }}
          </button>
          <button type="submit" class="btn btn-primary" :disabled="saving">
            {{ saving ? t('common.saving') : t('common.save') }}
          </button>
        </div>
      </header>

      <p v-if="localError" class="callout callout-error small" role="alert">{{ localError }}</p>
      <ErrorMessage v-if="saveError" :error="saveError" />

      <Panel :title="t('editor.profile.title')">
        <div class="stack">
          <div class="row" role="radiogroup" :aria-label="t('editor.profile.fields.type')">
            <label v-for="type in PROFILE_TYPES" :key="type" class="checkbox profile-type">
              <input v-model="form.profile.type" type="radio" name="profile-type" :value="type" />
              {{ t(PROFILE_TYPE_KEYS[type]) }}
            </label>
          </div>
          <p class="field-hint">
            {{
              form.profile.type === 'constant'
                ? t('editor.profile.hints.constant')
                : form.profile.type === 'ramp_up'
                  ? t('editor.profile.hints.ramp_up')
                  : t('editor.profile.hints.step')
            }}
          </p>
          <div v-if="form.profile.type === 'constant'" class="form-grid">
            <label class="field">
              <span class="field-label">{{ t('editor.profile.fields.rate') }}</span>
              <input v-model.number="form.profile.rate" class="input num" type="number" min="0.1" step="0.1" :aria-invalid="Boolean(profileErr('rate'))" />
              <span v-if="profileErr('rate')" class="field-error">{{ profileErr('rate') }}</span>
            </label>
            <label class="field">
              <span class="field-label">{{ t('editor.profile.fields.duration_sec') }}</span>
              <input v-model.number="form.profile.duration_sec" class="input num" type="number" min="1" step="1" :aria-invalid="Boolean(profileErr('duration_sec'))" />
              <span v-if="profileErr('duration_sec')" class="field-error">{{ profileErr('duration_sec') }}</span>
            </label>
          </div>
          <div v-else-if="form.profile.type === 'ramp_up'" class="form-grid">
            <label class="field">
              <span class="field-label">{{ t('editor.profile.fields.start_rate') }}</span>
              <input v-model.number="form.profile.start_rate" class="input num" type="number" min="0" step="0.1" :aria-invalid="Boolean(profileErr('start_rate'))" />
              <span v-if="profileErr('start_rate')" class="field-error">{{ profileErr('start_rate') }}</span>
            </label>
            <label class="field">
              <span class="field-label">{{ t('editor.profile.fields.end_rate') }}</span>
              <input v-model.number="form.profile.end_rate" class="input num" type="number" min="0.1" step="0.1" :aria-invalid="Boolean(profileErr('end_rate'))" />
              <span v-if="profileErr('end_rate')" class="field-error">{{ profileErr('end_rate') }}</span>
            </label>
            <label class="field">
              <span class="field-label">{{ t('editor.profile.fields.ramp_sec') }}</span>
              <input v-model.number="form.profile.ramp_sec" class="input num" type="number" min="1" step="1" :aria-invalid="Boolean(profileErr('ramp_sec'))" />
              <span v-if="profileErr('ramp_sec')" class="field-error">{{ profileErr('ramp_sec') }}</span>
            </label>
            <label class="field">
              <span class="field-label">{{ t('editor.profile.fields.hold_sec') }}</span>
              <input v-model.number="form.profile.hold_sec" class="input num" type="number" min="0" step="1" :aria-invalid="Boolean(profileErr('hold_sec'))" />
              <span v-if="profileErr('hold_sec')" class="field-error">{{ profileErr('hold_sec') }}</span>
            </label>
          </div>
          <div v-else class="form-grid">
            <label class="field">
              <span class="field-label">{{ t('editor.profile.fields.start_rate') }}</span>
              <input v-model.number="form.profile.step_start_rate" class="input num" type="number" min="0.1" step="0.1" :aria-invalid="Boolean(profileErr('start_rate'))" />
              <span v-if="profileErr('start_rate')" class="field-error">{{ profileErr('start_rate') }}</span>
            </label>
            <label class="field">
              <span class="field-label">{{ t('editor.profile.fields.step_rate') }}</span>
              <input v-model.number="form.profile.step_rate" class="input num" type="number" min="0.1" step="0.1" :aria-invalid="Boolean(profileErr('step_rate'))" />
              <span v-if="profileErr('step_rate')" class="field-error">{{ profileErr('step_rate') }}</span>
            </label>
            <label class="field">
              <span class="field-label">{{ t('editor.profile.fields.step_duration_sec') }}</span>
              <input v-model.number="form.profile.step_duration_sec" class="input num" type="number" min="5" max="3600" step="1" :aria-invalid="Boolean(profileErr('step_duration_sec'))" />
              <span v-if="profileErr('step_duration_sec')" class="field-error">{{ profileErr('step_duration_sec') }}</span>
            </label>
            <label class="field">
              <span class="field-label">{{ t('editor.profile.fields.max_steps') }}</span>
              <input v-model.number="form.profile.max_steps" class="input num" type="number" min="1" max="100" step="1" :aria-invalid="Boolean(profileErr('max_steps'))" />
              <span v-if="profileErr('max_steps')" class="field-error">{{ profileErr('max_steps') }}</span>
            </label>
            <label class="field">
              <span class="field-label">{{ t('editor.profile.fields.max_error_rate_pct') }}</span>
              <input v-model.number="form.profile.max_error_rate_pct" class="input num" type="number" min="0" max="100" step="0.1" :aria-invalid="Boolean(profileErr('max_error_rate'))" />
              <span v-if="profileErr('max_error_rate')" class="field-error">{{ profileErr('max_error_rate') }}</span>
            </label>
            <label class="field">
              <span class="field-label">{{ t('editor.profile.fields.max_p95_ms') }}</span>
              <input v-model.number="form.profile.max_p95_ms" class="input num" type="number" min="1" step="1" :placeholder="t('editor.optional')" :aria-invalid="Boolean(profileErr('max_p95_ms'))" />
              <span v-if="profileErr('max_p95_ms')" class="field-error">{{ profileErr('max_p95_ms') }}</span>
            </label>
          </div>
          <div v-if="effective.length" class="effective">
            <p class="field-label">{{ t('editor.profile.effective') }}</p>
            <p class="field-hint">{{ t('editor.profile.effectiveHint') }}</p>
            <dl class="dl">
              <template v-for="row in effective" :key="row.key">
                <dt>{{ row.label }}</dt>
                <dd class="num" :class="{ 'text-warning': row.clamped }">
                  {{ row.value }}
                  <span v-if="row.clamped" class="badge badge-warning">{{ t('editor.profile.clamped') }}</span>
                </dd>
              </template>
            </dl>
          </div>
        </div>
      </Panel>

      <Panel :title="t('editor.flow.title')" :description="t('editor.flow.count', { n: form.steps.length, max: MAX_STEPS })">
        <template #actions>
          <button type="button" class="btn btn-sm" :disabled="form.steps.length >= MAX_STEPS" @click="addStep">
            <Icon name="plus" :size="12" />{{ t('editor.flow.add') }}
          </button>
        </template>
        <div class="stack">
          <p class="field-hint">
            {{ t('editor.flow.templating') }} <code>{{ TEMPLATE_VAR }}</code> · <code>{{ TEMPLATE_SECRET }}</code>
          </p>
          <p v-if="errors.flow" class="field-error">{{ errors.flow.text }} <span v-if="errors.flow.detail" class="muted">{{ errors.flow.detail }}</span></p>
          <StepEditor
            v-for="(step, index) in form.steps"
            :key="step.uid"
            v-model="form.steps[index]!"
            :index="index"
            :count="form.steps.length"
            :errors="errors"
            @up="move(index, -1)"
            @down="move(index, 1)"
            @remove="removeStep(index)"
          />
          <p v-if="form.steps.length >= MAX_STEPS" class="field-hint">{{ t('editor.flow.max', { max: MAX_STEPS }) }}</p>
        </div>
      </Panel>

      <Panel :title="t('editor.slo.title')">
        <div class="form-grid">
          <label class="field">
            <span class="field-label">{{ t('editor.slo.p95') }}</span>
            <input v-model.number="form.sloP95Ms" class="input num" type="number" min="1" step="1" :placeholder="t('editor.optional')" :aria-invalid="Boolean(errors.slo_p95_ms)" />
            <span v-if="errors.slo_p95_ms" class="field-error">{{ errors.slo_p95_ms.text }}</span>
          </label>
          <label class="field">
            <span class="field-label">{{ t('editor.slo.errorRatePct') }}</span>
            <input v-model.number="form.sloErrorPct" class="input num" type="number" min="0" max="100" step="0.1" :placeholder="t('editor.optional')" :aria-invalid="Boolean(errors.slo_max_error_rate)" />
            <span v-if="errors.slo_max_error_rate" class="field-error">{{ errors.slo_max_error_rate.text }}</span>
          </label>
          <label class="field">
            <span class="field-label">{{ t('editor.slo.apdexT') }}</span>
            <input v-model.number="form.apdexTMs" class="input num" type="number" min="10" max="60000" step="10" :aria-invalid="Boolean(errors.apdex_t_ms)" />
            <span v-if="errors.apdex_t_ms" class="field-error">{{ errors.apdex_t_ms.text }}</span>
            <span v-else class="field-hint">{{ t('editor.slo.apdexHint') }}</span>
          </label>
        </div>
      </Panel>

      <Panel :title="t('editor.secrets.title')">
        <div class="stack">
          <p class="field-hint">{{ t('editor.secrets.hint') }}</p>
          <div v-if="missingSecrets.length" class="callout callout-warning small" role="status">
            <div class="stack-sm">
              <span>{{ t('editor.secrets.missing', { n: missingSecrets.length }, missingSecrets.length) }}</span>
              <div class="row">
                <button v-for="name in missingSecrets" :key="name" type="button" class="btn btn-sm mono" @click="addSecret(name)">
                  <Icon name="plus" :size="11" />{{ name }}
                </button>
              </div>
            </div>
          </div>
          <div v-for="(row, index) in secrets" :key="index" class="secret-row" :class="{ removed: row.remove }">
            <input
              v-model="row.name"
              class="input mono"
              :readonly="row.stored"
              maxlength="50"
              :placeholder="t('editor.secrets.name')"
              :aria-label="`${t('editor.secrets.name')} ${index + 1}`"
              :aria-invalid="Boolean(secretNameErrors[index])"
            />
            <input
              v-model="row.value"
              class="input mono"
              type="password"
              autocomplete="new-password"
              :disabled="row.remove"
              :placeholder="row.stored ? t('editor.secrets.storedPlaceholder') : t('editor.secrets.value')"
              :aria-label="`${t('editor.secrets.value')} ${row.name || index + 1}`"
            />
            <button type="button" class="btn btn-sm" @click="removeSecret(index)">
              {{ row.remove ? t('editor.secrets.undo') : t('common.delete') }}
            </button>
            <span v-if="secretNameErrors[index]" class="field-error full">{{ secretNameErrors[index] }}</span>
            <span v-else-if="row.remove" class="field-hint full">{{ t('editor.secrets.willRemove') }}</span>
          </div>
          <div><button type="button" class="btn btn-sm" @click="addSecret()"><Icon name="plus" :size="12" />{{ t('editor.secrets.add') }}</button></div>
        </div>
      </Panel>

      <Panel :title="t('dryRun.title')" :description="t('dryRun.hint')">
        <template #actions>
          <button type="button" class="btn btn-sm" :disabled="saving || dryRunning" @click="runDry">
            <Icon name="play" :size="12" />{{ dryRunning ? t('dryRun.running') : t('dryRun.button') }}
          </button>
        </template>
        <Skeleton v-if="dryRunning" :lines="3" />
        <ErrorMessage v-else-if="dryRunError" :error="dryRunError" />
        <DryRunResult v-else-if="dryRun" :result="dryRun" />
        <p v-else class="muted small">{{ t('dryRun.empty') }}</p>
      </Panel>

      <div v-if="scenarioId" class="row-between danger">
        <span class="small muted">{{ t('editor.deleteText') }}</span>
        <button type="button" class="btn btn-danger" @click="remove"><Icon name="trash" :size="13" />{{ t('editor.delete') }}</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.editor-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.name-field {
  flex: 1;
  min-width: 220px;
  max-width: 480px;
}

.name-input {
  font-size: var(--fs-lg);
  font-weight: 600;
}

.profile-type {
  padding: 4px 10px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
}

.effective {
  border-top: 1px solid var(--border);
  padding-top: var(--space-2);
}

.secret-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.5fr) auto;
  gap: var(--space-1);
}

.secret-row.removed input {
  text-decoration: line-through;
  opacity: 0.6;
}

.secret-row .full {
  grid-column: 1 / -1;
}

.danger {
  border-top: 1px solid var(--border);
  padding-top: var(--space-3);
}
</style>
