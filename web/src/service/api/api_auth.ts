// 本地账号鉴权与用户管理接口。
import { post, request } from '@/service/request'
import type { AuthUser, LoginResult, UserId, UserInfoRow, UserRole } from '@/types'

const put = (body: unknown): RequestInit => ({
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const authApi = {
  login(username: string, password: string) {
    return request<LoginResult>('/api/auth/login', post({ username, password }))
  },

  me() {
    return request<AuthUser>('/api/auth/me')
  },

  changePassword(oldPassword: string, newPassword: string) {
    return request<{ ok: boolean }>('/api/auth/change-password', post({ oldPassword, newPassword }))
  },

  logout() {
    return request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' })
  },

  switchUser(userId: UserId) {
    return request<LoginResult>('/api/auth/switch-user', post({ userId }))
  },

  listUsers() {
    return request<UserInfoRow[]>('/api/users')
  },

  createUser(body: { username: string; displayName: string; password: string; role: UserRole }) {
    return request<UserInfoRow>('/api/users', post(body))
  },

  updateUser(id: UserId, body: { displayName: string; role: UserRole }) {
    return request<UserInfoRow>(`/api/users/${encodeURIComponent(id)}`, put(body))
  },

  resetPassword(id: UserId, password: string) {
    return request<{ ok: boolean }>(`/api/users/${encodeURIComponent(id)}/reset-password`, post({ password }))
  },

  toggleUser(id: UserId) {
    return request<{ ok: boolean }>(`/api/users/${encodeURIComponent(id)}/toggle`, { method: 'POST' })
  },
}
