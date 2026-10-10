<script setup lang="ts">
// Ошибка загрузки или действия: переведённый текст по коду и кнопка «повторить».

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useErrorText } from '@/composables/useErrorText'
import Icon from './Icon.vue'

const props = withDefaults(defineProps<{ error: unknown; retry?: boolean }>(), { retry: false })
defineEmits<{ retry: [] }>()

const { t } = useI18n()
const { errorText } = useErrorText()
const text = computed(() => errorText(props.error))
</script>

<template>
  <div class="callout callout-error" role="alert">
    <Icon name="warning" />
    <div class="body">
      <span>{{ text }}</span>
      <button v-if="retry" type="button" class="btn btn-sm" @click="$emit('retry')">
        {{ t('common.retry') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.callout {
  color: var(--text);
}

.callout :deep(.icon) {
  color: var(--error);
  margin-top: 1px;
}

.body {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
</style>
