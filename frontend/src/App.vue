<script setup lang="ts">
// Корневой компонент: каркас для вошедших, голая страница для входа/регистрации,
// заголовок вкладки на текущем языке.

import { computed, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import AppShell from '@/components/AppShell.vue'
import ToastHost from '@/components/ToastHost.vue'

const route = useRoute()
const { t } = useI18n()
const bare = computed(() => route.meta.guest === true || !route.matched.length)

watchEffect(() => {
  const page = route.meta.titleKey ? t(route.meta.titleKey) : ''
  document.title = page ? t('app.titleWithPage', { page }) : t('app.name')
})
</script>

<template>
  <RouterView v-if="bare" />
  <AppShell v-else>
    <RouterView :key="route.path" />
  </AppShell>
  <ToastHost />
</template>
