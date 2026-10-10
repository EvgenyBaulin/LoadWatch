// Маршруты. Страницы грузятся лениво; вход и регистрация доступны без авторизации.

import { createRouter, createWebHistory } from 'vue-router'
import { tk } from '@/i18n'
import { useAuthStore } from '@/stores/auth'

declare module 'vue-router' {
  interface RouteMeta {
    /** Ключ перевода заголовка страницы (вкладка браузера и верхняя панель). */
    titleKey?: string
    /** Страница для гостей (вход, регистрация). */
    guest?: boolean
  }
}

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/sites' },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { guest: true, titleKey: tk('pages.login') },
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('@/views/RegisterView.vue'),
      meta: { guest: true, titleKey: tk('pages.register') },
    },
    {
      path: '/sites',
      name: 'sites',
      component: () => import('@/views/SitesView.vue'),
      meta: { titleKey: tk('pages.sites') },
    },
    {
      path: '/sites/:siteId',
      name: 'site',
      component: () => import('@/views/SiteView.vue'),
      meta: { titleKey: tk('pages.site') },
    },
    {
      path: '/sites/:siteId/dashboard',
      name: 'dashboard',
      component: () => import('@/views/DashboardView.vue'),
      meta: { titleKey: tk('pages.dashboard') },
    },
    {
      path: '/sites/:siteId/runs',
      name: 'runs',
      component: () => import('@/views/RunsView.vue'),
      meta: { titleKey: tk('pages.runs') },
    },
    {
      path: '/sites/:siteId/scenarios/new',
      name: 'scenario-new',
      component: () => import('@/views/ScenarioEditorView.vue'),
      meta: { titleKey: tk('pages.scenarioNew') },
    },
    {
      path: '/scenarios/:scenarioId/edit',
      name: 'scenario-edit',
      component: () => import('@/views/ScenarioEditorView.vue'),
      meta: { titleKey: tk('pages.scenarioEdit') },
    },
    {
      path: '/runs/:runId',
      name: 'run',
      component: () => import('@/views/RunView.vue'),
      meta: { titleKey: tk('pages.run') },
    },
    {
      path: '/channels',
      name: 'channels',
      component: () => import('@/views/ChannelsView.vue'),
      meta: { titleKey: tk('pages.channels') },
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/views/NotFoundView.vue'),
      meta: { titleKey: tk('pages.notFound') },
    },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.init()
  if (to.meta.guest) {
    return auth.isAuthenticated ? { name: 'sites' } : true
  }
  if (!auth.isAuthenticated) {
    return { name: 'login', query: to.fullPath !== '/sites' ? { redirect: to.fullPath } : {} }
  }
  return true
})
