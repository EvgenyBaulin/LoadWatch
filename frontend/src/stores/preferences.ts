// Тема и язык интерфейса: работают и без входа, выбор хранится в localStorage.

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { authApi } from '@/api/endpoints'
import type { Locale } from '@/api/types'
import { i18n, isLocale, LOCALE_STORAGE_KEY } from '@/i18n'

export type ThemeMode = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const THEME_STORAGE_KEY = 'lw.theme'

function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    // Без localStorage выбор действует до перезагрузки.
  }
}

export const usePreferencesStore = defineStore('preferences', () => {
  const savedTheme = readStorage(THEME_STORAGE_KEY)
  const themeMode = ref<ThemeMode>(
    savedTheme === 'light' || savedTheme === 'dark' ? savedTheme : 'system',
  )
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  const resolvedTheme = ref<ResolvedTheme>(resolve(themeMode.value))
  // Явный выбор языка пользователем (в этом браузере) важнее users.locale.
  const hasExplicitLocale = ref(isLocale(readStorage(LOCALE_STORAGE_KEY)))
  const locale = ref<Locale>(i18n.global.locale.value)

  function resolve(mode: ThemeMode): ResolvedTheme {
    if (mode === 'system') return media.matches ? 'dark' : 'light'
    return mode
  }

  function applyTheme(): void {
    const next = resolve(themeMode.value)
    // Сначала атрибут, потом реактивное значение: графики читают уже новые CSS-переменные.
    document.documentElement.setAttribute('data-theme', next)
    resolvedTheme.value = next
  }

  function setThemeMode(mode: ThemeMode): void {
    themeMode.value = mode
    writeStorage(THEME_STORAGE_KEY, mode)
    applyTheme()
  }

  /** Следующий режим по кругу: системная → светлая → тёмная. */
  function cycleTheme(): void {
    const order: ThemeMode[] = ['system', 'light', 'dark']
    const next = order[(order.indexOf(themeMode.value) + 1) % order.length] ?? 'system'
    setThemeMode(next)
  }

  media.addEventListener('change', () => {
    if (themeMode.value === 'system') applyTheme()
  })

  function applyLocale(next: Locale): void {
    i18n.global.locale.value = next
    locale.value = next
    document.documentElement.setAttribute('lang', next)
  }

  /**
   * Смена языка пользователем: мгновенно, с сохранением в localStorage,
   * а при входе в аккаунт — и в users.locale (на этом языке приходят уведомления).
   */
  async function setLocale(next: Locale, syncToServer: boolean): Promise<void> {
    applyLocale(next)
    hasExplicitLocale.value = true
    writeStorage(LOCALE_STORAGE_KEY, next)
    if (syncToServer) {
      try {
        await authApi.updatePreferences({ locale: next })
      } catch {
        // Язык интерфейса уже сменён; сервер синхронизируется при следующем входе.
      }
    }
  }

  applyTheme()
  applyLocale(locale.value)

  return {
    themeMode,
    resolvedTheme,
    locale,
    hasExplicitLocale,
    setThemeMode,
    cycleTheme,
    setLocale,
    applyLocale,
  }
})
