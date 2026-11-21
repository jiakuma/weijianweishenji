// 智能问答、Agent 会话、评测相关接口。
import { post, request, streamRequest } from '@/service/request'
import type { QAResult } from '@/types'

export const qaApi = {
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
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ module }),
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
    return request<{ ok: boolean }>(`/api/qa/agent/${confirmId}/decide`, post({ decision }))
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
    return request<{ ok: boolean; created?: number; total?: number; message?: string }>('/api/qa/eval/build', post({ count, category }))
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

  /** SSE 流式智能体问答：网络失败抛错，由调用方回退到 askQuestion。 */
  async streamAgentQuestion(
    question: string,
    mode: string,
    sessionId: string,
    handlers: import('@/types').AgentStreamHandlers,
    signal?: AbortSignal,
    module = 'assistant',
  ): Promise<void> {
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
        else if (type === 'answer_delta') handlers.onAnswerDelta?.(String(evt.text || ''))
        else if (type === 'done') handlers.onDone?.(evt.result as QAResult)
        else if (type === 'stopped') handlers.onStopped?.()
        else if (type === 'error') handlers.onError?.(String(evt.message || '运行失败'))
      }
    }
    await streamRequest(
      '/api/qa/agent/stream',
      { question, mode, sessionId, module },
      consume,
      signal,
    )
  },

  feedbackQA(id: string, feedback: string) {
    return request<{ ok: boolean }>(`/api/qa/${id}/feedback`, post({ feedback }))
  },
}
