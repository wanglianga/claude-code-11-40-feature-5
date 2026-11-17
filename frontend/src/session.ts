import { computed, ref } from 'vue'
import { api, clearSession, getStoredUser, setSession } from './api'
import type { User } from './types'

const currentUser = ref<User | null>(getStoredUser<User>())
const sessionVersion = ref(0)

export const authState = {
  user: currentUser,
  isAuthenticated: computed(() => Boolean(currentUser.value)),
  isWriter: computed(() =>
    currentUser.value ? ['researcher', 'auditor'].includes(currentUser.value.role) : false,
  ),
  version: sessionVersion,
}

export async function login(username: string, password: string): Promise<User> {
  const payload = await api.post<{ token: string; user: User }>('/auth/login', {
    username,
    password,
  })
  setSession(payload.token, payload.user)
  currentUser.value = payload.user
  sessionVersion.value += 1
  return payload.user
}

export function logout(): void {
  clearSession()
  currentUser.value = null
  sessionVersion.value += 1
}

export function roleLabel(role?: string): string {
  return (
    { researcher: '研究人员', auditor: '审核员', viewer: '只读访客' }[role ?? ''] ?? '未知角色'
  )
}

window.addEventListener('session-expired', () => {
  currentUser.value = null
  sessionVersion.value += 1
})
