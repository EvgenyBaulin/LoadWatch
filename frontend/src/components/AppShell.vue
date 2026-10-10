<script setup lang="ts">
// Каркас приложения для вошедшего пользователя: навигация слева, верхняя панель, страница.

import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import SideNav from './SideNav.vue'
import TopBar from './TopBar.vue'

const route = useRoute()
const { t } = useI18n()
const title = computed(() => (route.meta.titleKey ? t(route.meta.titleKey) : ''))
</script>

<template>
  <a href="#main" class="skip-link">{{ t('app.skipToContent') }}</a>
  <div class="shell">
    <SideNav />
    <div class="main-col">
      <TopBar :title="title" />
      <main id="main" tabindex="-1">
        <slot />
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
}

.main-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

main {
  flex: 1;
  min-width: 0;
}

main:focus {
  outline: none;
}

.skip-link {
  position: absolute;
  left: -9999px;
  top: 0;
  z-index: 100;
  background: var(--accent);
  color: var(--text-on-accent);
  padding: var(--space-2) var(--space-3);
}

.skip-link:focus {
  left: var(--space-2);
  top: var(--space-2);
}
</style>
