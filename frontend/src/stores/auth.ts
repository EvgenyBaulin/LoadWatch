// Авторизация: токен в localStorage, профиль пользователя, синхронизация языка с users.locale.

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ApiError, tokenStorage } from '@/api/client'
import { authApi } from '@/api/endpoints'
import type { AuthResponse, PreferencesUpdate, UserOut } from '@/api/types'
import { usePreferencesStore } from './preferences'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserOut | null>(null)
  const token = ref<string | null>(tokenStorage.get())
  let initPromise: Promise<void> | null = null

  const isAuthenticated = computed(() => token.value !== null && user.value !== null)

  /** Однократная загрузка профиля при старте приложения (если токен уже есть). */
  function init(): Promise<void> {
    initPromise ??= (async () => {
      if (!token.value) return
      try {
        user.value = await authApi.me()
        syncLocale(user.value)
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) logout()
        // Сеть недоступна — оставляем токен; страницы покажут ошибку загрузки.
      }
    })()
    return initPromise
  }

  function accept(response: AuthResponse): void {
    tokenStorage.set(response.access_token, response.expires_in)
    token.value = response.access_token
    user.value = response.user
    syncLocale(response.user)
  }

  /**
   * После входа: если язык в этом браузере не выбран явно — берём users.locale;
   * если выбран и отличается — отправляем его на сервер (уведомления на том же языке).
   */
  function syncLocale(profile: UserOut): void {
    const prefs = usePreferencesStore()
    if (!prefs.hasExplicitLocale) {
      prefs.applyLocale(profile.locale)
    } else if (prefs.locale !== profile.locale) {
      void updatePreferences({ locale: prefs.locale }).catch(() => undefined)
    }
  }

  async function login(email: string, password: string): Promise<void> {
    accept(await authApi.login(email, password))
  }

  async function register(email: string, password: string, inviteCode?: string): Promise<void> {
    const prefs = usePreferencesStore()
    accept(await authApi.register(email, password, prefs.locale, inviteCode))
  }

  async function updatePreferences(prefs: PreferencesUpdate): Promise<UserOut> {
    const updated = await authApi.updatePreferences(prefs)
    user.value = updated
    return updated
  }

  function logout(): void {
    tokenStorage.clear()
    token.value = null
    user.value = null
  }

  return { user, token, isAuthenticated, init, login, register, logout, updatePreferences }
})
