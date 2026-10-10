<script setup lang="ts">
// Обёртка страниц входа и регистрации: карточка по центру, язык и тема в углу.

import { useI18n } from 'vue-i18n'
import LanguageSwitcher from './LanguageSwitcher.vue'
import ThemeSwitcher from './ThemeSwitcher.vue'

defineProps<{ title: string }>()
const { t } = useI18n()
</script>

<template>
  <div class="auth">
    <header class="auth-top">
      <span class="brand">
        <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">
          <path d="M5 22h5l3-10 4 14 3-9 2 5h5" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        {{ t('app.name') }}
      </span>
      <div class="row">
        <LanguageSwitcher />
        <ThemeSwitcher />
      </div>
    </header>
    <main class="auth-main">
      <section class="card">
        <h1>{{ title }}</h1>
        <p class="muted small tagline">{{ t('app.tagline') }}</p>
        <slot />
      </section>
    </main>
  </div>
</template>

<style scoped>
.auth {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.auth-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--border);
  background: var(--bg-elevated);
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: 700;
  color: var(--accent-text);
}

.auth-main {
  flex: 1;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 10vh var(--space-3) var(--space-5);
}

.card {
  width: 100%;
  max-width: 380px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-top: 3px solid var(--accent);
  border-radius: var(--radius);
  padding: var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.tagline {
  margin-top: calc(-1 * var(--space-2));
}
</style>
