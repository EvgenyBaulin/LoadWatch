// Точка входа: Pinia, i18n, роутер, обработка 401.

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { onUnauthorized } from './api/client'
import { i18n } from './i18n'
import { router } from './router'
import { useAuthStore } from './stores/auth'
import { usePreferencesStore } from './stores/preferences'
import './styles/tokens.css'
import './styles/base.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(i18n)

// Тема и язык применяются сразу (инлайн-скрипт в index.html уже выставил их до загрузки).
usePreferencesStore()

onUnauthorized(() => {
  const auth = useAuthStore()
  if (!auth.token) return
  auth.logout()
  const current = router.currentRoute.value
  if (!current.meta.guest) {
    void router.push({ name: 'login', query: { redirect: current.fullPath } })
  }
})

app.use(router)
app.mount('#app')
