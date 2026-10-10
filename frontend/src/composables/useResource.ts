// Загрузка данных со статусами: loading / error / data и повторной загрузкой.

import { ref, shallowRef } from 'vue'

/**
 * Обёртка над асинхронной загрузкой. Ответы устаревших запросов отбрасываются
 * (если reload вызвали ещё раз, пока первый не вернулся).
 * `silent` — фоновое обновление без сброса данных и скелетона.
 */
export function useResource<T>(loader: () => Promise<T>, options: { immediate?: boolean } = {}) {
  const data = shallowRef<T | null>(null)
  const loading = ref(false)
  const error = shallowRef<unknown>(null)
  let seq = 0

  async function reload(silent = false): Promise<void> {
    const current = ++seq
    if (!silent) loading.value = true
    try {
      const value = await loader()
      if (current !== seq) return
      data.value = value
      error.value = null
    } catch (err) {
      if (current !== seq) return
      error.value = err
    } finally {
      if (current === seq) loading.value = false
    }
  }

  if (options.immediate !== false) void reload()

  return { data, loading, error, reload }
}
