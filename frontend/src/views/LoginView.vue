<script setup lang="ts">
// Вход по email и паролю.

import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import AuthLayout from '@/components/AuthLayout.vue'
import ErrorMessage from '@/components/ErrorMessage.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const email = ref('')
const password = ref('')
const busy = ref(false)
const error = ref<unknown>(null)

async function submit(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    await auth.login(email.value.trim(), password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/sites'
    // Только внутренние пути, чтобы ссылка ?redirect= не уводила на чужой сайт.
    await router.replace(redirect.startsWith('/') && !redirect.startsWith('//') ? redirect : '/sites')
  } catch (err) {
    error.value = err
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <AuthLayout :title="t('auth.loginTitle')">
    <form class="stack" novalidate @submit.prevent="submit">
      <ErrorMessage v-if="error" :error="error" />
      <label class="field">
        <span class="field-label">{{ t('auth.email') }}</span>
        <input v-model="email" name="email" class="input" type="email" autocomplete="email" required autofocus />
      </label>
      <label class="field">
        <span class="field-label">{{ t('auth.password') }}</span>
        <input v-model="password" name="password" class="input" type="password" autocomplete="current-password" required />
      </label>
      <button type="submit" class="btn btn-primary" :disabled="busy || !email || !password">
        {{ busy ? t('common.loading') : t('auth.login') }}
      </button>
      <p class="small muted">
        {{ t('auth.noAccount') }}
        <RouterLink to="/register">{{ t('auth.register') }}</RouterLink>
      </p>
    </form>
  </AuthLayout>
</template>
