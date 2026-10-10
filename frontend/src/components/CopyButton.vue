<script setup lang="ts">
// Кнопка «скопировать» с подтверждением для экранного диктора.

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Icon from './Icon.vue'

const props = defineProps<{ text: string; label?: string }>()
const { t } = useI18n()
const copied = ref(false)

async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.text)
  } catch {
    // Без доступа к буферу (http, старый браузер) — выделяем текст через временное поле.
    const area = document.createElement('textarea')
    area.value = props.text
    document.body.appendChild(area)
    area.select()
    document.execCommand('copy')
    area.remove()
  }
  copied.value = true
  window.setTimeout(() => (copied.value = false), 1600)
}
</script>

<template>
  <button type="button" class="btn btn-sm" :aria-label="label ?? t('common.copy')" @click="copy">
    <Icon :name="copied ? 'check' : 'copy'" :size="13" />
    <span>{{ copied ? t('common.copied') : t('common.copy') }}</span>
    <span class="sr-only" aria-live="polite">{{ copied ? t('common.copied') : '' }}</span>
  </button>
</template>
