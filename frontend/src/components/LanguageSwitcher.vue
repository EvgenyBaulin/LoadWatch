<script setup lang="ts">
// Переключатель языка (ru / en): мгновенно, сохраняется локально и в профиле.

import { useI18n } from 'vue-i18n'
import type { Locale } from '@/api/types'
import { SUPPORTED_LOCALES } from '@/i18n'
import { useAuthStore } from '@/stores/auth'
import { usePreferencesStore } from '@/stores/preferences'

const prefs = usePreferencesStore()
const auth = useAuthStore()
const { t } = useI18n()

function select(locale: Locale): void {
  if (locale === prefs.locale) return
  void prefs.setLocale(locale, auth.isAuthenticated)
}
</script>

<template>
  <div class="lang" role="group" :aria-label="t('language.switch')">
    <button
      v-for="loc in SUPPORTED_LOCALES"
      :key="loc"
      type="button"
      class="lang-btn"
      :class="{ active: prefs.locale === loc }"
      :aria-pressed="prefs.locale === loc"
      :lang="loc"
      :title="loc === 'ru' ? t('language.ru') : t('language.en')"
      @click="select(loc)"
    >
      {{ loc === 'ru' ? t('language.ruShort') : t('language.enShort') }}
    </button>
  </div>
</template>

<style scoped>
.lang {
  display: inline-flex;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  overflow: hidden;
  height: 26px;
}

.lang-btn {
  all: unset;
  cursor: pointer;
  padding: 0 8px;
  font-size: var(--fs-xs);
  font-weight: 600;
  color: var(--text-muted);
  display: inline-flex;
  align-items: center;
}

.lang-btn + .lang-btn {
  border-left: 1px solid var(--border-strong);
}

.lang-btn.active {
  background: var(--accent-soft);
  color: var(--accent-text);
}

.lang-btn:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
</style>
