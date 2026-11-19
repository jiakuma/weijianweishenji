import type {
  AssetItem,
  AuditModule,
  BackupItem,
  CategoryItem,
  ConflictConfig,
  ConflictTaskDetail,
  ConflictTaskItem,
  DocQuery,
  ImportTask,
  KnowledgeDoc,
  QAResult,
  Settings,
  StorageInfo,
  UploadItem,
} from '@/types'

const BASE = String(import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')
const TOKEN_KEY = 'kb-access-token'

// 令牌只放会话级存储：关闭标签页即失效，不写 localStorage
export function getAccessToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) || ''
}
export function setAccessToken(token: string): void {
  if (token) sessionStorage.setItem(TOKEN_KEY, token)
  else sessionStorage.removeItem(TOKEN_KEY)
}

function authHeaders(extra?: HeadersInit): HeadersInit {
  const token = getAccessToken()
  return { 'Content-Type': 'application/json', ...(token ? { 'X-Access-Token': token } : {}), ...(extra ?? {}) }
}

function failMessage(status: number, path: string): string {
  if (status === 401) return '访问令牌无效或未配置，请到系统设置里重新输入'
  if (status === 403) return '仅允许本机执行此操作'
  return `${path} → ${status}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { ...init, headers: authHeaders(init?.headers) })
  if (!res.ok) throw new Error(failMessage(res.status, path))
  return (await res.json()) as T
}

const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) })

export const fileUrl = (path: string) => `${BASE}${path}`

export const api = {
  async health(): Promise<boolean> {
    try {
      const res = await fetch(`${BASE}/api/health`, { signal: AbortSignal.timeout(3000) })
      return res.ok
    } catch {
      return false
    }
  },

  listDocs(query: DocQuery = {}) {
    return request<KnowledgeDoc[]>('/api/docs/query', post(query))
  },

  getDoc(id: string) {
    return request<KnowledgeDoc>(`/api/docs/${id}`)
  },

  getFulltext(id: string) {
    return request<{ docId: string; text: string; charCount: number }>(`/api/docs/${id}/fulltext`)
  },

  getAssets(id: string) {
    return request<AssetItem[]>(`/api/docs/${id}/assets`)
  },

  toggleStar(id: string) {
    return request<{ starred: boolean }>(`/api/docs/${id}/star`, { method: 'POST' })
  },

  setCategory(id: string, category: string) {
    return request<{ ok: boolean }>(`/api/docs/${id}/category`, post({ category }))
  },

  setTags(id: string, tags: string[]) {
    return request<{ ok: boolean }>(`/api/docs/${id}/tags`, post({ tags }))
  },

  async deleteDoc(id: string, purge: boolean) {
    await request(`/api/docs/${id}?purge=${purge}`, { method: 'DELETE' })
  },

  reparseDoc(id: string) {
    return request<{ ok: boolean; taskId?: string }>(`/api/docs/${id}/reparse`, { method: 'POST' })
  },

  exportDoc(id: string) {
    return request<{ filename: string; content: string }>(`/api/docs/${id}/export`)
  },

  categories() {
    return request<{ items: CategoryItem[]; tree: import('@/types').CategoryNode[]; total: number }>('/api/categories')
  },

  addCategory(name: string, parentId: string) {
    return request<{ ok: boolean; item?: CategoryItem; message?: string }>('/api/categories', post({ name, parentId }))
  },

  renameCategory(id: string, name: string) {
    return request<{ ok: boolean; message?: string }>(`/api/categories/${id}`, {
      method: 'PUT', body: JSON.stringify({ name }),
    })
  },

  async deleteCategory(id: string) {
    await request(`/api/categories/${id}`, { method: 'DELETE' })
  },

  listTasks() {
    return request<ImportTask[]>('/api/imports')
  },

  async uploadFiles(items: UploadItem[], source = '本地文件', batchId = ''): Promise<ImportTask[]> {
    const form = new FormData()
    for (const item of items) {
      form.append('files', item.file, item.file.name)
    }
    form.append('relative_paths', JSON.stringify(items.map((i) => i.relativePath)))
    form.append('categories', JSON.stringify(items.map((i) => i.category || '')))
    form.append('source', source)
    form.append('batch_id', batchId)
    const token = getAccessToken()
    const res = await fetch(`${BASE}/api/imports/files`, {
      method: 'POST', body: form,
      headers: token ? { 'X-Access-Token': token } : {},
    })
    if (!res.ok) throw new Error(failMessage(res.status, '上传'))
    return (await res.json()) as ImportTask[]
  },

  /** 智能助手附件上传：只进后端临时区，不写资料库；返回 tmp- 开头的临时文件 ID。 */
  async uploadAssistantFiles(items: UploadItem[], sessionId = ''): Promise<
    Array<{ id: string; name: string; filename: string; sizeKb: number }>
  > {
    const form = new FormData()
    for (const item of items) {
      form.append('files', item.file, item.file.name)
    }
    form.append('session_id', sessionId)
    const token = getAccessToken()
    const res = await fetch(`${BASE}/api/assistant/uploads`, {
      method: 'POST', body: form,
      headers: token ? { 'X-Access-Token': token } : {},
    })
    if (!res.ok) throw new Error(failMessage(res.status, '上传附件'))
    return (await res.json()) as Array<{ id: string; name: string; filename: string; sizeKb: number }>
  },

  uploadLink(url: string, name: string) {
    return request<ImportTask>('/api/imports/links', post({ url, name, source: '网页链接' }))
  },

  retryTask(id: string) {
    return request<{ ok: boolean }>(`/api/imports/${id}/retry`, { method: 'POST' })
  },

  reparseTask(id: string) {
    return request<{ ok: boolean }>(`/api/imports/${id}/reparse`, { method: 'POST' })
  },

  clearTasks(scope: 'finished' | 'all') {
    return request<{ ok: boolean; removed: number }>(`/api/imports?scope=${scope}`, { method: 'DELETE' })
  },

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
    return request<{ ok: boolean; message: string; moved: number }>('/api/storage/location', post({ originalsDir }))
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

  askQuestion(question: string, mode = '混合', sessionId = '') {
    return request<QAResult>('/api/qa/ask', post({ question, mode, sessionId }))
  },

  listQA() {
    return request<QAResult[]>('/api/qa')
  },

  listSessions(module = '') {
    const q = module ? `?module=${encodeURIComponent(module)}` : ''
    return request<import('@/types').QASession[]>(`/api/qa/sessions${q}`)
  },

  createSession(module = 'assistant') {
    return request<import('@/types').QASession>('/api/qa/sessions', {
      method: 'POST', body: JSON.stringify({ module }),
    })
  },

  deleteSession(id: string) {
    return request<{ ok: boolean }>(`/api/qa/sessions/${id}`, { method: 'DELETE' })
  },

  getSession(id: string) {
    return request<QAResult[]>(`/api/qa/sessions/${id}`)
  },

  listAgentTools() {
    return request<{ items: import('@/types').AgentTool[] }>('/api/agent/tools')
  },

  stopAgent(runId: string) {
    return request<{ ok: boolean }>(`/api/qa/agent/${runId}/stop`, { method: 'POST' })
  },

  decideConfirm(confirmId: string, decision: 'approve' | 'reject') {
    return request<{ ok: boolean }>(`/api/qa/agent/${confirmId}/decide`,
                                   post({ decision }))
  },

  agentCapability() {
    return request<{ hasLlm: boolean; nativeToolCalls: boolean }>('/api/qa/agent/capability')
  },

  listAgentRuns() {
    return request<import('@/types').AgentRunItem[]>('/api/qa/agent/runs')
  },

  listAgentSteps(runId: string) {
    return request<import('@/types').AgentStepItem[]>(`/api/qa/agent/runs/${runId}/steps`)
  },

  buildEvalSet(count: number, category = '') {
    return request<{ ok: boolean; created?: number; total?: number; message?: string }>(
      '/api/qa/eval/build', post({ count, category }))
  },

  runEval(k = 5, mode = '混合') {
    return request<import('@/types').EvalRunResult>('/api/qa/eval/run', post({ k, mode }))
  },

  listEvalQueries() {
    return request<{ items: import('@/types').EvalQueryItem[]; total: number }>('/api/qa/eval/queries')
  },

  clearEvalQueries() {
    return request<{ ok: boolean; removed: number }>('/api/qa/eval/queries', { method: 'DELETE' })
  },

  listEvalRuns() {
    return request<import('@/types').EvalRunItem[]>('/api/qa/eval/runs')
  },

  /** SSE 流式智能体问答；网络/HTTP 层失败时抛错，由调用方回退到 askQuestion。 */
  async streamAgentQuestion(question: string, mode: string, sessionId: string,
                            handlers: import('@/types').AgentStreamHandlers,
                            signal?: AbortSignal, module = 'assistant'): Promise<void> {
    const res = await fetch(`${BASE}/api/qa/agent/stream`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ question, mode, sessionId, module }),
      signal,
    })
    if (!res.ok || !res.body) throw new Error(failMessage(res.status, '智能体流'))
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    const consume = (chunk: string) => {
      for (const line of chunk.split('\n')) {
        const trimmed = line.trim()
        if (!trimmed.startsWith('data:')) continue
        let evt: Record<string, unknown>
        try { evt = JSON.parse(trimmed.slice(5).trim()) } catch { continue }
        const type = evt.type as string
        const kind = evt.kind as string
        if (kind === 'conflict' || kind === 'conflict_done') {
          handlers.onConflictProgress?.(evt as unknown as import('@/types').ConflictProgress)
        } else if (kind === 'contract' || kind === 'contract_done') {
          handlers.onContractProgress?.(evt as unknown as import('@/types').ContractProgress)
        } else if (kind === 'workpaper' || kind === 'workpaper_done') {
          handlers.onWorkpaperProgress?.(evt as unknown as import('@/types').WorkpaperProgress)
        } else if (type === 'start') handlers.onStart?.(evt.runId as string, evt.qaId as string | undefined)
        else if (type === 'step') handlers.onStep?.(evt.action as import('@/types').AgentAction)
        else if (type === 'confirm') {
          const { confirmId, tool, label, args, risk } = evt
          handlers.onConfirm?.({ confirmId, tool, label, args, risk } as import('@/types').AgentConfirm)
        } else if (type === 'progress') {
          const { taskId, taskName, status, progress, message } = evt
          handlers.onProgress?.({ taskId, taskName, status, progress, message } as import('@/types').AgentProgress)
        } else if (type === 'status') handlers.onStatus?.(evt.steps as number)
        else if (type === 'done') handlers.onDone?.(evt.result as QAResult)
        else if (type === 'stopped') handlers.onStopped?.()
        else if (type === 'error') handlers.onError?.(String(evt.message || '运行失败'))
      }
    }
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const p of parts) consume(p)
    }
    if (buffer.trim()) consume(buffer)
  },

  feedbackQA(id: string, feedback: string) {
    return request<{ ok: boolean }>(`/api/qa/${id}/feedback`, post({ feedback }))
  },

  listAuditModules() {
    return request<AuditModule[]>('/api/modules')
  },

  conflictConfig() {
    return request<ConflictConfig>('/api/modules/conflict/config')
  },

  async conflictAnalyze(files: File[], sessionId?: string) {
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    if (sessionId) form.append('session_id', sessionId)
    const token = getAccessToken()
    const res = await fetch(`${BASE}/api/modules/conflict/analyze`, {
      method: 'POST',
      headers: token ? { 'X-Access-Token': token } : {},
      body: form,
    })
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({}))) as { error?: string }
      throw new Error(detail.error || failMessage(res.status, '开始检测'))
    }
    return (await res.json()) as { task_id: string; mode: string; budget?: number }
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
