<script setup lang="ts">
// Меню пользователя: email, ссылка на настройки и выход.

import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'
import Icon from './Icon.vue'

const auth = useAuthStore()
const router = useRouter()
const { t } = useI18n()
const open = ref(false)
const root = ref<HTMLElement | null>(null)

function onDocClick(event: MouseEvent): void {
  if (open.value && root.value && !root.value.contains(event.target as Node)) open.value = false
}

function onKey(event: KeyboardEvent): void {
  if (event.key === 'Escape') open.value = false
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKey)
})

function logout(): void {
  auth.logout()
  void router.push({ name: 'login' })
}
</script>

<template>
  <div ref="root" class="user-menu">
    <button
      type="button"
      class="btn btn-ghost btn-icon"
      :aria-expanded="open"
      aria-haspopup="true"
      :aria-label="t('user.menu')"
      :title="auth.user?.email"
      @click="open = !open"
    >
      <Icon name="user" />
    </button>
    <div v-if="open" class="menu" role="menu">
      <p class="email" :title="auth.user?.email">{{ auth.user?.email }}</p>
      <RouterLink to="/channels" class="menu-item" role="menuitem" @click="open = false">
        <Icon name="bell" :size="14" />
        {{ t('nav.channels') }}
      </RouterLink>
      <button type="button" class="menu-item" role="menuitem" @click="logout">
        <Icon name="logout" :size="14" />
        {{ t('user.logout') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.user-menu {
  position: relative;
}

.menu {
  position: absolute;
  right: 0;
  top: calc(100% + 4px);
  min-width: 200px;
  background: var(--surface);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: var(--space-1) 0;
  z-index: 40;
}

.email {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.menu-item {
  all: unset;
  box-sizing: border-box;
  display: flex;
  width: 100%;
  align-items: center;
  gap: var(--space-2);
  padding: 6px var(--space-3);
  font-size: var(--fs-sm);
  color: var(--text);
  cursor: pointer;
}

.menu-item:hover,
.menu-item:focus-visible {
  background: var(--surface-hover);
  text-decoration: none;
}

.menu-item:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}
</style>
