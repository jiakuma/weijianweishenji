// 历史审计问题定性库接口：分页浏览、详情、检索与统计。
import { post, request } from '@/service/request'
import type { AuditIssue, AuditIssueStats } from '@/types'

export interface AuditIssueListParams {
  page: number
  size: number
  year: string
  category: string
  subject: string
  q: string
}

export interface AuditIssueListResult {
  items: AuditIssue[]
  total: number
  page: number
  size: number
}

function listQuery(params: AuditIssueListParams): string {
  const search = new URLSearchParams({
    page: String(params.page),
    size: String(params.size),
    year: params.year || '',
    category: params.category || '',
    subject: params.subject || '',
    q: params.q || '',
  })
  return search.toString()
}

export const issuesApi = {
  listAuditIssues(params: AuditIssueListParams) {
    return request<AuditIssueListResult>(`/api/audit-issues/list?${listQuery(params)}`)
  },

  getAuditIssue(id: string) {
    return request<AuditIssue>(`/api/audit-issues/${encodeURIComponent(id)}`)
  },

  searchAuditIssues(query: string, limit = 10) {
    return request<{ query: string; total: number; items: AuditIssue[]; notice?: string }>(
      '/api/audit-issues/search', post({ query, limit }),
    )
  },

  auditIssueStats() {
    return request<AuditIssueStats>('/api/audit-issues/stats')
  },
}
