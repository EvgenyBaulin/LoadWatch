<script setup lang="ts">
// Бейдж статуса запуска или верификации сайта; цвет — по смыслу.

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { RunStatus, VerificationStatus } from '@/api/types'
import { RUN_STATUS_KEYS, VERIFICATION_STATUS_KEYS } from '@/i18n/labels'

const props = defineProps<
  { kind: 'run'; status: RunStatus } | { kind: 'site'; status: VerificationStatus }
>()

const { t } = useI18n()

const view = computed(() => {
  if (props.kind === 'run') {
    const tone: Record<RunStatus, string> = {
      queued: 'neutral',
      running: 'info',
      finished: 'success',
      failed: 'error',
      cancelled: 'warning',
    }
    return { tone: tone[props.status], text: t(RUN_STATUS_KEYS[props.status]) }
  }
  const tone: Record<VerificationStatus, string> = {
    pending: 'warning',
    verified: 'success',
    unverified: 'error',
  }
  return { tone: tone[props.status], text: t(VERIFICATION_STATUS_KEYS[props.status]) }
})
</script>

<template>
  <span class="badge" :class="`badge-${view.tone}`">
    <span class="dot" aria-hidden="true" />
    {{ view.text }}
  </span>
</template>

<style scoped>
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
</style>
