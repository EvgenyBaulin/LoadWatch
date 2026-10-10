<script setup lang="ts">
// Верхняя панель: заголовок страницы, место для элементов страницы (дашборд телепортирует
// туда выбор сайта и времени), переключатели языка и темы, меню пользователя.

import LanguageSwitcher from './LanguageSwitcher.vue'
import ThemeSwitcher from './ThemeSwitcher.vue'
import UserMenu from './UserMenu.vue'

defineProps<{ title: string }>()
</script>

<template>
  <header class="topbar">
    <h1 class="title">{{ title }}</h1>
    <div id="topbar-controls" class="controls" />
    <div class="global">
      <LanguageSwitcher />
      <ThemeSwitcher />
      <UserMenu />
    </div>
  </header>
</template>

<style scoped>
.topbar {
  min-height: var(--topbar-height);
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-1) var(--space-3);
  background: var(--bg-elevated);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 15;
  flex-wrap: wrap;
}

.title {
  font-size: var(--fs-lg);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 40ch;
}

.controls {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: 1;
  flex-wrap: wrap;
  min-width: 0;
}

.global {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  margin-left: auto;
}

@media (max-width: 640px) {
  .title {
    max-width: 50vw;
    font-size: var(--fs-md);
  }

  .controls {
    order: 3;
    flex-basis: 100%;
  }
}
</style>
