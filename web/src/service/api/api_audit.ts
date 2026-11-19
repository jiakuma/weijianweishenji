// 审计模块（制度互斥 / 合同审计 / 底稿审核）相关接口。
import { fileUrl, request, uploadRequest } from '@/service/request'
import type { AuditModule, ConflictConfig, ConflictTaskDetail, ConflictTaskItem } from '@/types'

export const auditApi = {
  listAuditModules() {
    return request<AuditModule[]>('/api/modules')
  },

  conflictConfig() {
    return request<ConflictConfig>('/api/modules/conflict/config')
  },

  conflictAnalyze(files: File[], sessionId?: string) {
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    if (sessionId) form.append('session_id', sessionId)
    return uploadRequest<{ task_id: string; mode: string; budget?: number }>(
      '/api/modules/conflict/analyze', form,
    )
  },

  conflictSessions() {
    return request<import('@/types').ConflictSession[]>('/api/modules/conflict/sessions')
  },

  createConflictSession() {
    return request<import('@/types').ConflictSession>('/api/modules/conflict/sessions', { method: 'POST' })
  },

  deleteConflictSession(id: string) {
    return request<{ ok: boolean }>(`/api/modules/conflict/sessions/${id}`, { method: 'DELETE' })
  },

  conflictTasks() {
    return request<ConflictTaskItem[]>('/api/modules/conflict/tasks')
  },

  conflictTask(id: string) {
    return request<ConflictTaskDetail>(`/api/modules/conflict/tasks/${id}`)
  },

  deleteConflictTask(id: string) {
    return request<{ ok: boolean }>(`/api/modules/conflict/tasks/${id}`, { method: 'DELETE' })
  },

  conflictExportUrl(id: string) {
    return fileUrl(`/api/modules/conflict/tasks/${id}/export`)
  },

  contractExportUrl(id: string) {
    return fileUrl(`/api/modules/contract/tasks/${id}/export`)
  },

  workpaperExportUrl(id: string) {
    return fileUrl(`/api/modules/workpaper/tasks/${id}/export`)
  },
}
