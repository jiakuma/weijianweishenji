import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, getAccessToken, setAccessToken } from '@/api'
import { useKnowledgeStore } from '@/stores/knowledge'
import type { AuthUser, LoginResult, UserId } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const currentUser = ref<AuthUser | null>(null)
  const isAdmin = computed(() => currentUser.value?.role === 'admin')
  const mustChangePassword = computed(() => currentUser.value?.mustChangePassword === true)

  /** 清空当前身份，同时丢弃按人隔离的问答会话，避免下一个账号看到上一个账号的会话。 */
  function clearUser(): void {
    currentUser.value = null
    try {
      useKnowledgeStore().clearQaState()
    } catch {
      // Pinia 尚未安装时忽略，调用方会在正常生命周期内重试。
    }
  }

  async function fetchMe(): Promise<AuthUser | null> {
    if (!getAccessToken()) {
      clearUser()
      return null
    }
    try {
      currentUser.value = await api.me()
      return currentUser.value
    } catch {
      clearUser()
      return null
    }
  }

  async function login(username: string, password: string): Promise<LoginResult> {
    const result = await api.login(username, password)
    setAccessToken(result.token)
    currentUser.value = result.user
    return result
  }

  /** 管理员切换到目标账号：换 token、换身份，并丢弃上一个账号的问答会话状态。 */
  async function switchUser(userId: UserId): Promise<LoginResult> {
    const result = await api.switchUser(userId)
    setAccessToken(result.token)
    currentUser.value = result.user
    useKnowledgeStore().clearQaState()
    return result
  }

  async function logout(): Promise<void> {
    try {
      await api.logout()
    } catch {
      // 后端不可达时也要清理本地身份。
    }
    setAccessToken('')
    clearUser()
  }

  function markPasswordChanged(): void {
    if (currentUser.value) {
      currentUser.value = { ...currentUser.value, mustChangePassword: false }
    }
  }

  return {
    currentUser, isAdmin, mustChangePassword,
    fetchMe, login, switchUser, logout, clearUser, markPasswordChanged,
  }
})
