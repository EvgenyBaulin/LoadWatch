<script setup lang="ts">
// Один шаг потока: метод, путь, заголовки, тело, извлечение переменных, проверки, пауза.

import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { BodyType, ExtractSource } from '@/api/types'
import type { FieldError } from '@/composables/useErrorText'
import { tk } from '@/i18n'
import { BODY_TYPE_KEYS, EXTRACT_SOURCE_KEYS } from '@/i18n/labels'
import { HTTP_METHODS, jsonError, type StepForm } from '@/utils/scenarioForm'
import Icon from '@/components/Icon.vue'
import KeyValueEditor from './KeyValueEditor.vue'

const props = defineProps<{
  index: number
  count: number
  errors: Record<string, FieldError>
}>()
const emit = defineEmits<{ up: []; down: []; remove: [] }>()
const step = defineModel<StepForm>({ required: true })
const { t } = useI18n()
const expanded = ref(true)

const BODY_TYPES: (BodyType | 'none')[] = ['none', 'json', 'form', 'raw']
const SOURCES: ExtractSource[] = ['json', 'header', 'cookie', 'regex']

const prefix = computed(() => `flow.${props.index}`)
const id = computed(() => `step-${step.value.uid}`)

function err(field: string): FieldError | undefined {
  return props.errors[`${prefix.value}.${field}`]
}

const stepErrors = computed(() =>
  Object.entries(props.errors).filter(([path]) => path === prefix.value || path.startsWith(`${prefix.value}.`)),
)

const bodyJsonError = computed(() => (step.value.bodyType === 'json' ? jsonError(step.value.bodyText) : null))
const pathRelativeError = computed(() => {
  const path = step.value.path.trim()
  return path && !path.startsWith('/') ? t('editor.step.pathRelative') : null
})

function addRule(): void {
  step.value.extract = [...step.value.extract, { var: '', source: 'json', expr: '' }]
}

function removeRule(index: number): void {
  step.value.extract = step.value.extract.filter((_, i) => i !== index)
}

const EXPR_PLACEHOLDER: Record<ExtractSource, string> = {
  json: tk('editor.extract.placeholders.json'),
  header: tk('editor.extract.placeholders.header'),
  cookie: tk('editor.extract.placeholders.cookie'),
  regex: tk('editor.extract.placeholders.regex'),
}
</script>

<template>
  <article class="step" :aria-labelledby="`${id}-title`" :class="{ invalid: stepErrors.length || bodyJsonError || pathRelativeError }">
    <header class="step-head">
      <button
        type="button"
        class="btn btn-ghost btn-sm toggle"
        :aria-expanded="expanded"
        :aria-controls="`${id}-body`"
        @click="expanded = !expanded"
      >
        <Icon name="chevronDown" :size="12" :class="{ collapsed: !expanded }" />
        <span :id="`${id}-title`" class="step-title">
          {{ t('editor.step.title', { n: index + 1 }) }}
          <span class="mono muted">{{ step.method }} {{ step.path }}</span>
        </span>
      </button>
      <div class="row">
        <button type="button" class="btn btn-ghost btn-icon btn-sm" :disabled="index === 0" :aria-label="t('editor.step.moveUp', { n: index + 1 })" @click="emit('up')">
          <Icon name="up" :size="13" />
        </button>
        <button type="button" class="btn btn-ghost btn-icon btn-sm" :disabled="index === count - 1" :aria-label="t('editor.step.moveDown', { n: index + 1 })" @click="emit('down')">
          <Icon name="down" :size="13" />
        </button>
        <button type="button" class="btn btn-ghost btn-icon btn-sm" :disabled="count === 1" :aria-label="t('editor.step.remove', { n: index + 1 })" @click="emit('remove')">
          <Icon name="trash" :size="13" />
        </button>
      </div>
    </header>

    <div v-show="expanded" :id="`${id}-body`" class="step-body">
      <div class="grid-main">
        <label class="field">
          <span class="field-label">{{ t('editor.step.name') }}</span>
          <input v-model="step.name" class="input" maxlength="100" required :aria-invalid="Boolean(err('name'))" />
          <span v-if="err('name')" class="field-error">{{ err('name')?.text }}</span>
        </label>
        <label class="field">
          <span class="field-label">{{ t('editor.step.method') }}</span>
          <select v-model="step.method" class="select">
            <option v-for="m in HTTP_METHODS" :key="m" :value="m">{{ m }}</option>
          </select>
        </label>
        <label class="field path">
          <span class="field-label">{{ t('editor.step.path') }}</span>
          <input
            v-model="step.path"
            class="input mono"
            maxlength="2000"
            required
            :placeholder="t('editor.step.pathPlaceholder')"
            :aria-invalid="Boolean(err('path') || pathRelativeError)"
          />
          <span v-if="pathRelativeError" class="field-error">{{ pathRelativeError }}</span>
          <span v-if="err('path')" class="field-error">{{ err('path')?.text }} <span v-if="err('path')?.detail" class="muted">{{ err('path')?.detail }}</span></span>
        </label>
      </div>

      <KeyValueEditor
        v-model="step.headers"
        :legend="t('editor.step.headers')"
        :key-placeholder="t('editor.step.headerName')"
        :value-placeholder="t('editor.step.headerValue')"
        :error="err('headers') ? `${err('headers')?.text} ${err('headers')?.detail ?? ''}` : undefined"
      />

      <fieldset class="stack-sm">
        <legend>{{ t('editor.body.title') }}</legend>
        <div class="row" role="radiogroup" :aria-label="t('editor.body.type')">
          <label v-for="type in BODY_TYPES" :key="type" class="checkbox">
            <input v-model="step.bodyType" type="radio" :name="`${id}-body-type`" :value="type" />
            {{ t(BODY_TYPE_KEYS[type]) }}
          </label>
        </div>
        <template v-if="step.bodyType === 'json' || step.bodyType === 'raw'">
          <textarea
            v-model="step.bodyText"
            class="textarea"
            rows="5"
            spellcheck="false"
            :aria-label="t('editor.body.content')"
            :aria-invalid="Boolean(bodyJsonError || err('body'))"
            :placeholder="step.bodyType === 'json' ? t('editor.body.jsonPlaceholder') : t('editor.body.rawPlaceholder')"
          />
          <span v-if="bodyJsonError" class="field-error" role="alert">{{ t('editor.body.invalidJson', { error: bodyJsonError }) }}</span>
          <span v-else-if="step.bodyType === 'json' && step.bodyText.trim()" class="field-hint text-success">{{ t('editor.body.validJson') }}</span>
        </template>
        <KeyValueEditor
          v-if="step.bodyType === 'form'"
          v-model="step.bodyForm"
          :legend="t('editor.body.formFields')"
          :key-placeholder="t('editor.body.fieldName')"
          :value-placeholder="t('editor.body.fieldValue')"
        />
        <label v-if="step.bodyType !== 'none'" class="field ct">
          <span class="field-label">{{ t('editor.body.contentType') }}</span>
          <input v-model="step.contentType" class="input mono" maxlength="200" :placeholder="t('editor.body.contentTypePlaceholder')" />
        </label>
        <span v-if="err('body')" class="field-error">{{ err('body')?.text }} <span v-if="err('body')?.detail" class="muted">{{ err('body')?.detail }}</span></span>
      </fieldset>

      <fieldset class="stack-sm">
        <legend>{{ t('editor.extract.title') }}</legend>
        <p class="field-hint">{{ t('editor.extract.hint') }}</p>
        <div v-for="(rule, ri) in step.extract" :key="ri" class="extract-row">
          <input v-model="rule.var" class="input mono" maxlength="50" :placeholder="t('editor.extract.var')" :aria-label="`${t('editor.extract.var')} ${ri + 1}`" :aria-invalid="Boolean(err(`extract.${ri}.var`))" />
          <select v-model="rule.source" class="select" :aria-label="`${t('editor.extract.source')} ${ri + 1}`">
            <option v-for="src in SOURCES" :key="src" :value="src">{{ t(EXTRACT_SOURCE_KEYS[src]) }}</option>
          </select>
          <input v-model="rule.expr" class="input mono" maxlength="500" :placeholder="t(EXPR_PLACEHOLDER[rule.source])" :aria-label="`${t('editor.extract.expr')} ${ri + 1}`" :aria-invalid="Boolean(err(`extract.${ri}.expr`))" />
          <button type="button" class="btn btn-ghost btn-icon btn-sm" :aria-label="t('common.removeRow', { n: ri + 1 })" @click="removeRule(ri)">
            <Icon name="trash" :size="13" />
          </button>
          <span v-if="err(`extract.${ri}.var`) || err(`extract.${ri}.expr`)" class="field-error full">
            {{ (err(`extract.${ri}.var`) ?? err(`extract.${ri}.expr`))?.text }}
          </span>
        </div>
        <div><button type="button" class="btn btn-sm" @click="addRule"><Icon name="plus" :size="12" />{{ t('editor.extract.add') }}</button></div>
      </fieldset>

      <div class="grid-checks">
        <label class="field">
          <span class="field-label">{{ t('editor.step.expectedStatus') }}</span>
          <input v-model="step.expectedStatus" class="input mono" :placeholder="t('editor.step.expectedStatusPlaceholder')" :aria-invalid="Boolean(err('expected_status'))" />
          <span v-if="err('expected_status')" class="field-error">{{ err('expected_status')?.text }}</span>
          <span v-else class="field-hint">{{ t('editor.step.expectedStatusHint') }}</span>
        </label>
        <label class="field">
          <span class="field-label">{{ t('editor.step.assertContains') }}</span>
          <input v-model="step.assertContains" class="input" maxlength="1000" :placeholder="t('editor.step.assertPlaceholder')" />
        </label>
        <label class="field">
          <span class="field-label">{{ t('editor.step.thinkMs') }}</span>
          <input v-model.number="step.thinkMs" class="input num" type="number" min="0" max="60000" step="100" :aria-invalid="Boolean(err('think_ms'))" />
          <span v-if="err('think_ms')" class="field-error">{{ err('think_ms')?.text }}</span>
        </label>
      </div>

      <ul v-if="stepErrors.length" class="other-errors">
        <li v-for="[path, e] in stepErrors" :key="path" class="field-error">
          <code>{{ path }}</code> {{ e.text }} <span v-if="e.detail" class="muted">{{ e.detail }}</span>
        </li>
      </ul>
    </div>
  </article>
</template>

<style scoped>
.step {
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: var(--radius);
  background: var(--surface-2);
}

.step.invalid {
  border-left-color: var(--error);
}

.step-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-2);
  border-bottom: 1px solid var(--border);
}

.toggle {
  height: auto;
  min-height: 24px;
  min-width: 0;
  justify-content: flex-start;
  overflow: hidden;
}

.step-title {
  font-weight: 600;
  display: inline-flex;
  gap: var(--space-2);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.collapsed {
  transform: rotate(-90deg);
}

.step-body {
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.grid-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 110px minmax(0, 2fr);
  gap: var(--space-2);
}

.grid-checks {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--space-2);
}

.extract-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 130px minmax(0, 2fr) auto;
  gap: var(--space-1);
}

.extract-row .full {
  grid-column: 1 / -1;
}

.ct {
  max-width: 320px;
}

.other-errors {
  margin: 0;
  padding-left: var(--space-4);
}

@media (max-width: 720px) {
  .grid-main,
  .extract-row {
    grid-template-columns: 1fr;
  }
}
</style>
