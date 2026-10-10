<script setup lang="ts">
// Переключатель темы: системная → светлая → тёмная. Иконка и aria-label — текущий режим.

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { usePreferencesStore } from '@/stores/preferences'
import Icon from './Icon.vue'

const prefs = usePreferencesStore()
const { t } = useI18n()

const icon = computed(() =>
  prefs.themeMode === 'light' ? 'sun' : prefs.themeMode === 'dark' ? 'moon' : 'system',
)
const modeText = computed(() =>
  prefs.themeMode === 'light'
    ? t('theme.light')
    : prefs.themeMode === 'dark'
      ? t('theme.dark')
      : t('theme.system'),
)
const label = computed(() => t('theme.switch', { mode: modeText.value }))
</script>

<template>
  <button type="button" class="btn btn-ghost btn-icon" :aria-label="label" :title="label" @click="prefs.cycleTheme()">
    <Icon :name="icon" />
  </button>
</template>
