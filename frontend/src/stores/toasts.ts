// Короткие уведомления («сохранено», «скопировано»), озвучиваются через aria-live.

import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface Toast {
  id: number
  kind: 'success' | 'error' | 'info'
  text: string
}

export const useToastStore = defineStore('toasts', () => {
  const items = ref<Toast[]>([])
  let seq = 0

  function push(kind: Toast['kind'], text: string, timeoutMs = 4000): void {
    const id = ++seq
    items.value.push({ id, kind, text })
    window.setTimeout(() => dismiss(id), timeoutMs)
  }

  function dismiss(id: number): void {
    items.value = items.value.filter((toast) => toast.id !== id)
  }

  return {
    items,
    success: (text: string) => push('success', text),
    error: (text: string) => push('error', text, 7000),
    info: (text: string) => push('info', text),
    dismiss,
  }
})
