<script setup lang="ts">
// Список пар «ключ — значение» (заголовки, поля формы).

import { useI18n } from 'vue-i18n'
import type { KeyValue } from '@/utils/scenarioForm'
import Icon from '@/components/Icon.vue'

defineProps<{ legend: string; keyPlaceholder: string; valuePlaceholder: string; error?: string }>()
const items = defineModel<KeyValue[]>({ required: true })
const { t } = useI18n()

function add(): void {
  items.value = [...items.value, { key: '', value: '' }]
}

function remove(index: number): void {
  items.value = items.value.filter((_, i) => i !== index)
}
</script>

<template>
  <fieldset class="kv">
    <legend>{{ legend }}</legend>
    <div v-for="(item, index) in items" :key="index" class="kv-row">
      <input v-model="item.key" class="input mono" :placeholder="keyPlaceholder" :aria-label="`${legend}: ${keyPlaceholder} ${index + 1}`" />
      <input v-model="item.value" class="input mono" :placeholder="valuePlaceholder" :aria-label="`${legend}: ${valuePlaceholder} ${index + 1}`" />
      <button type="button" class="btn btn-ghost btn-icon btn-sm" :aria-label="t('common.removeRow', { n: index + 1 })" @click="remove(index)">
        <Icon name="trash" :size="13" />
      </button>
    </div>
    <p v-if="error" class="field-error">{{ error }}</p>
    <button type="button" class="btn btn-sm" @click="add"><Icon name="plus" :size="12" />{{ t('common.addRow') }}</button>
  </fieldset>
</template>

<style scoped>
.kv {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  align-items: flex-start;
}

.kv-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.5fr) auto;
  gap: var(--space-1);
  width: 100%;
}
</style>
