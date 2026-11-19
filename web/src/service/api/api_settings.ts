// 系统设置、存储与备份相关接口。
import { post, request } from '@/service/request'
import type { BackupItem, Settings, StorageInfo } from '@/types'

export const settingsApi = {
  getSettings() {
    return request<Settings>('/api/settings')
  },

  saveSettings(patch: Partial<Settings>) {
    return request<Settings>('/api/settings', post(patch))
  },

  testModel(body: { baseUrl?: string; apiKey?: string; model?: string }) {
    return request<{ ok: boolean; message: string }>('/api/settings/test-model', post(body))
  },

  getStorage() {
    return request<StorageInfo>('/api/storage')
  },

  changeLocation(originalsDir: string) {
    return request<{ ok: boolean; message: string; moved: number }>(
      '/api/storage/location', post({ originalsDir }),
    )
  },

  listBackups() {
    return request<BackupItem[]>('/api/storage/backups')
  },

  createBackup() {
    return request<{ ok: boolean; name: string }>('/api/storage/backup', { method: 'POST' })
  },

  restoreBackup(name: string) {
    return request<{ ok: boolean; message: string }>('/api/storage/restore', post({ name }))
  },

  zhihuLoginStart() {
    return request<{ ok: boolean; message: string }>('/api/zhihu/login/start', { method: 'POST' })
  },

  zhihuLoginStatus() {
    return request<{ state: string; message: string }>('/api/zhihu/login/status')
  },

  zhihuLoginCancel() {
    return request<{ ok: boolean }>('/api/zhihu/login/cancel', { method: 'POST' })
  },
}
