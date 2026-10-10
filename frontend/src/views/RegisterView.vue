<script setup lang="ts">
// Регистрация: email, пароль (от 8 символов), необязательный инвайт-код.

import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import AuthLayout from '@/components/AuthLayout.vue'
import ErrorMessage from '@/components/ErrorMessage.vue'
import { useErrorText } from '@/composables/useErrorText'
import { useAuthStore } from '@/stores/auth'

const MIN_PASSWORD = 8

const auth = useAuthStore()
const router = useRouter()
const { t } = useI18n()
const { fieldErrors } = useErrorText()

const email = ref('')
const password = ref('')
const inviteCode = ref('')
const busy = ref(false)
const error = ref<unknown>(null)
const touched = ref(false)

const passwordTooShort = computed(() => touched.value && password.value.length < MIN_PASSWORD)
const fields = computed(() => fieldErrors(error.value))

async function submit(): Promise<void> {
  touched.value = true
  if (password.value.length < MIN_PASSWORD) return
  busy.value = true
  error.value = null
  try {
    await auth.register(email.value.trim(), password.value, inviteCode.value.trim() || undefined)
    await router.replace('/sites')
  } catch (err) {
    error.value = err
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <AuthLayout :title="t('auth.registerTitle')">
    <form class="stack" novalidate @submit.prevent="submit">
      <ErrorMessage v-if="error" :error="error" />
      <label class="field">
        <span class="field-label">{{ t('auth.email') }}</span>
        <input
          v-model="email"
          class="input"
          type="email"
          autocomplete="email"
          required
          autofocus
          :aria-invalid="Boolean(fields.email)"
        />
        <span v-if="fields.email" class="field-error">{{ fields.email.text }}</span>
      </label>
      <label class="field">
        <span class="field-label">{{ t('auth.password') }}</span>
        <input
          v-model="password"
          class="input"
          type="password"
          autocomplete="new-password"
          required
          :minlength="MIN_PASSWORD"
          :aria-invalid="passwordTooShort"
          aria-describedby="password-hint"
          @blur="touched = true"
        />
        <span id="password-hint" :class="passwordTooShort ? 'field-error' : 'field-hint'">
          {{ t('auth.passwordHint', { n: MIN_PASSWORD }) }}
        </span>
      </label>
      <label class="field">
        <span class="field-label">{{ t('auth.inviteCode') }}</span>
        <input v-model="inviteCode" class="input" type="text" autocomplete="off" :placeholder="t('auth.inviteCodePlaceholder')" />
        <span class="field-hint">{{ t('auth.inviteCodeHint') }}</span>
      </label>
      <button type="submit" class="btn btn-primary" :disabled="busy || !email || !password">
        {{ busy ? t('common.loading') : t('auth.register') }}
      </button>
      <p class="small muted">
        {{ t('auth.haveAccount') }}
        <RouterLink to="/login">{{ t('auth.login') }}</RouterLink>
      </p>
    </form>
  </AuthLayout>
</template>
