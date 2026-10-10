<script setup lang="ts">
// Страница 404.

import { useI18n } from 'vue-i18n'
import EmptyState from '@/components/EmptyState.vue'

const { t } = useI18n()
</script>

<template>
  <div class="page">
    <EmptyState :title="t('notFound.title')" :text="t('notFound.text')">
      <RouterLink to="/sites" class="btn btn-primary">{{ t('notFound.back') }}</RouterLink>
    </EmptyState>
  </div>
</template>
