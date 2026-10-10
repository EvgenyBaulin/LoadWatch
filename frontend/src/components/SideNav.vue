<script setup lang="ts">
// Узкая левая навигация: общие разделы и разделы текущего сайта.

import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Icon, { type IconName } from './Icon.vue'

const route = useRoute()
const { t } = useI18n()

interface NavItem {
  to: string
  label: string
  icon: IconName
  active: boolean
}

const siteId = computed(() => {
  const id = route.params.siteId
  return typeof id === 'string' ? id : null
})

const items = computed<NavItem[]>(() => {
  const list: NavItem[] = [
    { to: '/sites', label: t('nav.sites'), icon: 'sites', active: route.name === 'sites' },
  ]
  if (siteId.value) {
    const base = `/sites/${siteId.value}`
    list.push(
      { to: `${base}/dashboard`, label: t('nav.dashboard'), icon: 'dashboard', active: route.name === 'dashboard' },
      { to: `${base}/runs`, label: t('nav.runs'), icon: 'runs', active: route.name === 'runs' },
      { to: base, label: t('nav.siteSettings'), icon: 'shield', active: route.name === 'site' },
    )
  }
  list.push({ to: '/channels', label: t('nav.channels'), icon: 'bell', active: route.name === 'channels' })
  return list
})
</script>

<template>
  <nav class="sidenav" :aria-label="t('nav.main')">
    <RouterLink to="/sites" class="brand" :aria-label="t('app.home')">
      <svg viewBox="0 0 32 32" width="26" height="26" aria-hidden="true">
        <path
          d="M5 22h5l3-10 4 14 3-9 2 5h5"
          fill="none"
          stroke="currentColor"
          stroke-width="2.6"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
    </RouterLink>
    <ul class="items">
      <li v-for="item in items" :key="item.to">
        <RouterLink
          :to="item.to"
          class="item"
          :class="{ active: item.active }"
          :aria-current="item.active ? 'page' : undefined"
          :title="item.label"
        >
          <Icon :name="item.icon" :size="18" />
          <span class="label">{{ item.label }}</span>
        </RouterLink>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.sidenav {
  width: var(--nav-width);
  flex-shrink: 0;
  background: var(--bg-elevated);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  align-items: stretch;
  position: sticky;
  top: 0;
  height: 100vh;
  z-index: 20;
}

.brand {
  height: var(--topbar-height);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--accent-text);
  border-bottom: 1px solid var(--border);
}

.items {
  list-style: none;
  margin: 0;
  padding: var(--space-2) 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 7px 2px;
  color: var(--text-muted);
  border-left: 2px solid transparent;
  text-decoration: none;
}

.item:hover {
  color: var(--text);
  background: var(--surface-hover);
  text-decoration: none;
}

.item.active {
  color: var(--accent-text);
  border-left-color: var(--accent);
  background: var(--accent-soft);
}

.label {
  font-size: 9.5px;
  line-height: 1.1;
  text-align: center;
  max-width: 52px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 640px) {
  .sidenav {
    width: 48px;
  }

  .label {
    display: none;
  }

  .item {
    padding: 10px 2px;
  }
}
</style>
