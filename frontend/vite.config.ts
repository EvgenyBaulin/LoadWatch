import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Dev-сервер проксирует /api на локальный бэкенд (адрес можно сменить переменной
// LOADWATCH_API_URL); в проде статику и API отдаёт один Nginx с одного origin.
const apiTarget = process.env.LOADWATCH_API_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  define: {
    // Флаги сборки vue-i18n: только Composition API, без devtools в проде.
    __VUE_I18N_FULL_INSTALL__: true,
    __VUE_I18N_LEGACY_API__: false,
    __INTLIFY_PROD_DEVTOOLS__: false,
  },
  server: {
    proxy: {
      '/api': { target: apiTarget, changeOrigin: false },
    },
  },
  preview: {
    proxy: {
      '/api': { target: apiTarget, changeOrigin: false },
    },
  },
  build: {
    target: 'es2022',
    chunkSizeWarningLimit: 700,
    rolldownOptions: {
      output: {
        // Графики — отдельным чанком: страницы входа и списков их не грузят.
        codeSplitting: {
          groups: [
            { name: 'charts', priority: 10, test: /node_modules[\\/](chart\.js|vue-chartjs|chartjs-adapter-date-fns|date-fns|@kurkle)/ },
            { name: 'vendor', priority: 20, test: /node_modules[\\/](vue|@vue|vue-router|pinia|vue-i18n|@intlify)[\\/]/ },
          ],
        },
      },
    },
  },
})
