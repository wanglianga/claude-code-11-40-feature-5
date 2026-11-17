import { ref } from 'vue'

export interface ToastItem {
  id: number
  message: string
  kind: 'ok' | 'error'
}

const items = ref<ToastItem[]>([])
let counter = 0

export function useToast() {
  function push(message: string, kind: 'ok' | 'error' = 'ok') {
    const id = ++counter
    items.value.push({ id, message, kind })
    window.setTimeout(() => {
      items.value = items.value.filter((item) => item.id !== id)
    }, kind === 'error' ? 6000 : 3200)
  }
  return { items, push }
}

export const toasts = items
