<template>
  <div class="qa-shell" :class="{ 'is-empty': !messages.length }">
    <div ref="listEl" class="qa-list">
      <div v-if="!messages.length" class="qa-empty">
        <div class="qa-empty-hint">{{ cfg.empty }}</div>
        <div class="qa-suggests">
          <button v-for="q in cfg.suggests" :key="q" class="qa-suggest" @click="useSuggest(q)">{{ q }}</button>
        </div>
      </div>

      <div v-for="(m, i) in messages" :key="i" class="qa-msg" :class="m.role">
        <div v-if="m.role === 'user'" class="bubble user">
          <div>{{ displayQuestion(m.text) }}</div>
          <div v-if="m.files?.length" class="user-files">
            <span v-for="(f, fi) in m.files" :key="fi" class="user-file-chip">{{ f }}</span>
          </div>
        </div>

        <div v-else-if="m.loading" class="bubble bot loading">
          <span class="dot" /><span class="dot" /><span class="dot" /> {{ t('assistant.thinking') }}
        </div>

        <div v-else-if="m.error" class="bubble bot error">{{ m.error }}</div>

        <div v-else-if="m.result" class="bubble bot">
          <div v-if="m.streaming && !m.answerText && !m.contract && !m.conflict && !m.workpaper" class="bubble-inline-loading">
            <span class="dot" /><span class="dot" /><span class="dot" /> {{ m.conflictStage || m.contractStage || m.workpaperStage || t('assistant.analyzing') }}
          </div>
          <div v-else-if="(m.answerText || m.result?.answer?.markdown || m.result?.answer?.conclusion) && !m.conflict && !m.contract && !m.workpaper" class="qa-markdown">
            <MarkdownView
              :text="m.answerText || m.result?.answer?.markdown || m.result?.answer?.conclusion || ''"
              @cite="(n: number) => openCitation(i, n)"
            />
            <span v-if="m.streaming && m.answerText" class="typing-cursor" />
          </div>

          <div v-else-if="m.contract" class="contract-report">
            <div class="contract-report-head">
              <span>合同审计：{{ m.contract.totalIssues }} 项待关注</span>
              <el-button size="small" plain @click="exportContract(m.contract.taskId)">{{ t('common.export') }}</el-button>
            </div>
            <div v-if="m.contract.contractType" class="contract-sub">合同类型 · {{ m.contract.contractType }}</div>
            <div v-if="m.contract.issues?.length" class="contract-section">文本审核</div>
            <div v-for="it in m.contract.issues" :key="it.index" class="contract-issue">
              <div class="contract-issue-head">
                <span class="contract-no">{{ it.index }}</span>
                <span class="contract-title">{{ it.title }}</span>
                <span class="contract-level" :class="levelClass(it.level)">{{ it.level }}</span>
              </div>
              <div class="contract-kv"><b>{{ it.dimension }} · {{ it.locate }}</b></div>
              <div class="contract-kv">{{ it.fact }}</div>
              <div v-if="it.quote" class="contract-quote">{{ it.quote }}</div>
              <div class="contract-kv">{{ it.analysis }}</div>
              <div class="contract-kv">建议：{{ it.suggestion }}</div>
            </div>
            <div v-if="m.contract.reconciliation?.length" class="contract-section">跨文件勾稽</div>
            <div v-for="(r, ri) in m.contract.reconciliation" :key="ri" class="contract-issue">
              <div class="contract-title">{{ r.field }}</div>
              <div class="contract-kv">{{ r.files }}</div>
              <div class="contract-kv">{{ r.value_a }} ↔ {{ r.value_b }}</div>
              <div class="contract-kv">{{ r.note }}</div>
            </div>
            <div v-if="!m.contract.totalIssues" class="qa-conclusion">未发现需要关注的问题</div>
          </div>

          <div v-else-if="m.workpaper" class="contract-report">
            <div class="contract-report-head">
              <span>底稿审核：符合 {{ m.workpaper.summary?.pass ?? 0 }} · 不符合 {{ m.workpaper.summary?.fail ?? 0 }} · 无法判断 {{ m.workpaper.summary?.unknown ?? 0 }}</span>
              <el-button size="small" plain @click="exportWorkpaper(m.workpaper.taskId)">{{ t('common.export') }}</el-button>
            </div>
            <div class="contract-sub">被审计单位 {{ m.workpaper.unitCount || 0 }} 家 · 审计底稿 {{ m.workpaper.workpaperCount || 0 }} 份</div>
            <div v-if="m.workpaper.summary?.conclusion" class="qa-conclusion">{{ m.workpaper.summary.conclusion }}</div>
            <div v-for="a in m.workpaper.assessments" :key="a.id" class="contract-issue">
              <div class="contract-issue-head">
                <span class="contract-no">{{ a.id }}</span>
                <span class="contract-title">{{ a.requirement }}</span>
                <span class="contract-level" :class="assessClass(a.result)">{{ a.result }}</span>
              </div>
              <div class="contract-kv">{{ a.reason }}</div>
              <div v-for="(ev, ei) in a.evidence" :key="ei" class="contract-quote">{{ ev }}</div>
            </div>
            <div v-if="!m.workpaper.summary?.total" class="qa-conclusion">未能从上位文件中提取到可核对的要求点</div>
          </div>

          <div v-else-if="m.conflict && !m.conflict.conflicts?.length" class="qa-conclusion">
            <span>{{ m.conflict.conflictCount ? `检出 ${m.conflict.conflictCount} 处互斥条款` : '未检出互斥条款' }}</span>
            <el-button v-if="m.conflict.conflictCount" size="small" plain style="margin-left:10px" @click="exportConflict(m.conflict.taskId)">{{ t('common.export') }}</el-button>
          </div>

          <div v-if="m.conflict && m.conflict.conflicts?.length" class="conflict-report">
            <div class="conflict-report-head">
              <span>检出 {{ m.conflict.conflictCount }} 处互斥条款</span>
              <el-button size="small" plain @click="exportConflict(m.conflict.taskId)">{{ t('common.export') }}</el-button>
            </div>
            <div v-for="c in m.conflict.conflicts" :key="c.index" class="conflict-row">
              <div class="conflict-row-head">
                <span class="conflict-no">{{ c.index }}</span>
                <span class="conflict-topic">{{ c.topic }}</span>
              </div>
              <div class="conflict-exp">{{ c.explanation }}</div>
              <div class="conflict-quote"><b>甲 · {{ c.a.locate }}</b><div>{{ c.a.text }}</div></div>
              <div class="conflict-quote"><b>乙 · {{ c.b.locate }}</b><div>{{ c.b.text }}</div></div>
            </div>
          </div>

          <div v-if="m.progress?.length" class="qa-progress">
            <div v-for="p in m.progress" :key="p.taskId" class="qa-progress-row">
              <span>{{ p.taskName || p.taskId }}</span>
              <span>{{ p.status }} · {{ p.progress }}%</span>
            </div>
          </div>

          <div v-if="m.confirm" class="qa-confirm" :class="{ high: m.confirm.risk === 'high' }">
            <div class="qa-confirm-head">
              <span class="qa-confirm-tag">{{ m.confirm.risk === 'high' ? '高风险操作' : '待确认' }}</span>
              <span>{{ m.confirm.label }} · {{ m.confirm.tool }}</span>
            </div>
            <pre class="qa-confirm-args">{{ JSON.stringify(m.confirm.args, null, 2) }}</pre>
            <div v-if="m.confirm.risk === 'high'" class="qa-confirm-warn">会删除数据，部分操作不可恢复</div>
            <div class="qa-confirm-btns">
              <el-button size="small" :type="m.confirm.risk === 'high' ? 'danger' : 'primary'" @click="decideConfirm(i, 'approve')">同意执行</el-button>
              <el-button size="small" @click="decideConfirm(i, 'reject')">拒绝</el-button>
            </div>
          </div>

          <div v-if="m.result.citations?.length && !m.conflict" class="qa-cites">
            <span class="qa-cites-label">引用</span>
            <button
              v-for="(c, ci) in m.result.citations"
              :key="ci"
              class="cite-chip"
              :title="c.quote"
              @click="openDoc(c)"
            >《{{ c.docTitle }}》第 {{ c.page }} 页</button>
          </div>

          <div v-if="!m.streaming && m.result?.id && m.result.status !== 'running'" class="qa-feedback">
            <button class="fb-btn" :class="{ active: m.result?.feedback === 'good' }" :title="t('common.good')" @click="rate(i, 'good')">👍</button>
            <button class="fb-btn" :class="{ active: m.result?.feedback === 'bad' }" :title="t('common.bad')" @click="rate(i, 'bad')">👎</button>
            <button class="fb-btn fb-retry" :title="t('common.retry')" @click="retryMessage(i)">↻ 重试</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="picked.length" class="qa-picked">
      <el-tag v-for="(f, i) in picked" :key="`${f.name}-${i}`" closable size="small" @close="picked.splice(i, 1)">
        {{ f.name }}
      </el-tag>
    </div>
    <div class="qa-input">
      <el-upload :auto-upload="false" :show-file-list="false" multiple :accept="ACCEPT" :on-change="onPick">
        <el-button circle text :icon="Paperclip" :disabled="sending" />
      </el-upload>
      <el-input
        v-model="input"
        type="textarea"
        :rows="2"
        resize="none"
        :placeholder="t('assistant.placeholder')"
        @keydown.enter.exact.prevent="send"
      />
      <el-button v-if="sending" type="danger" plain @click="stopStream">{{ t('assistant.stop') }}</el-button>
      <el-button v-else type="primary" :disabled="!input.trim() && !picked.length" :loading="uploading" @click="send">{{ t('assistant.send') }}</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onActivated, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Paperclip } from '@element-plus/icons-vue'
import { ElMessage, type UploadProps } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { api } from '@/api'
import MarkdownView from '@/components/MarkdownView.vue'
import { useKnowledgeStore } from '@/stores/knowledge'
import type {
  AgentConfirm, AgentProgress, ConflictDetail, ConflictRow,
  ContractDetail, WorkpaperDetail, QACitation, QAResult,
} from '@/types'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const store = useKnowledgeStore()

// 四个入口共用聊天组件，靠路由 meta.module 区分会话域与引导语
const moduleKey = computed(() => {
  const m = String(route.meta.module || '')
  return ['assistant', 'conflict', 'contract', 'workpaper'].includes(m) ? m : 'assistant'
})
const moduleConfig: Record<string, { empty: string; suggests: string[]; defaultQ: string }> = {
  assistant: {
    empty: t('assistant.guide.assistant'),
    suggests: ['总结最近导入的资料要点', '对比各文档里的方案差异', '找出与某某主题相关的关键结论'],
    defaultQ: '请分析这些文件',
  },
  conflict: {
    empty: t('assistant.guide.conflict'),
    suggests: ['检查上传制度之间有没有互斥', '这两份制度对同一事项的规定是否打架'],
    defaultQ: '请对这些文件做制度互斥检测',
  },
  contract: {
    empty: t('assistant.guide.contract'),
    suggests: ['帮我看看合同有没有什么问题', '核对合同与附件的关键字段是否一致'],
    defaultQ: '请帮我审计这些合同文件',
  },
  workpaper: {
    empty: t('assistant.guide.workpaper'),
    suggests: ['帮我复核这些审计工作底稿', '检查底稿是否覆盖实施方案要求的事项'],
    defaultQ: '请帮我审核这些底稿',
  },
}
const cfg = computed(() => moduleConfig[moduleKey.value])

type AssistantMsg = { role: 'assistant'; loading?: boolean; streaming?: boolean; answerText?: string;
                      error?: string; result?: QAResult; confirm?: AgentConfirm;
                      progress?: AgentProgress[]; conflictStage?: string; conflict?: ConflictDetail;
                      contractStage?: string; contract?: ContractDetail
                      workpaperStage?: string; workpaper?: WorkpaperDetail }
type Msg = { role: 'user'; text: string; files?: string[] } | AssistantMsg

const input = ref('')
const sending = ref(false)
const messages = ref<Msg[]>([])
const listEl = ref<HTMLElement | null>(null)
const abortController = ref<AbortController | null>(null)
const currentRunId = ref('')

const sessionId = ref('')
const picked = ref<File[]>([])
const uploading = ref(false)

const ACCEPT = '.pdf,.docx,.doc,.txt,.md,.xlsx,.xls'
const onPick: UploadProps['onChange'] = (file) => {
  const raw = file.raw
  if (!raw) return
  if (picked.value.some((f) => f.name === raw.name && f.size === raw.size)) return
  picked.value.push(raw)
}

async function uploadPicked(): Promise<string[]> {
  if (!picked.value.length) return []
  uploading.value = true
  try {
    // 只上传到助手临时区，不经过 /api/imports/files，不产生资料库记录。
    // 同步返回 tmp- 文件 ID，无需再轮询导入任务拿 docId。
    const rows = await api.uploadAssistantFiles(
      picked.value.map((f) => ({ file: f, relativePath: f.name })), sessionId.value)
    const ids = rows.map((r) => r.id).filter(Boolean)
    if (ids.length) picked.value = []
    return ids
  } finally {
    uploading.value = false
  }
}

// 提问文本里拼接的文档 ID 属技术信息，展示给用户时剔除。
function displayQuestion(text: string): string {
  return (text || '').replace(/\n*\s*待检测的文档\s*ID[：:][^\n]*/gi, '').trim()
}

function scrollToEnd() {
  nextTick(() => {
    const el = listEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

async function loadSessions() {
  try {
    await store.loadQaSessions(moduleKey.value)
  } catch {
    /* 列表加载失败不阻断问答 */
  }
}

function rowsToMessages(rows: import('@/types').QAResult[]): Msg[] {
  return rows.flatMap((r): Msg[] => [
    { role: 'user', text: r.question, files: r.files },
    { role: 'assistant', result: r, conflict: r.conflict, contract: r.contract, workpaper: r.workpaper, streaming: r.status === 'running' },
  ])
}

async function switchSession(id: string) {
  sessionId.value = id
  store.qaSessionId = id
  try {
    messages.value = rowsToMessages(await api.getSession(id))
  } catch {
    messages.value = []
  }
  scrollToEnd()
}

async function newSession() {
  const id = await store.createQaSession(moduleKey.value)
  if (!id) {
    ElMessage.error('新建会话失败')
    return
  }
  sessionId.value = id
  messages.value = []
}

function useSuggest(q: string) {
  input.value = q
  void send()
}

async function send() {
  let q = input.value.trim()
  if ((!q || sending.value) && !picked.value.length) return
  // 先确保会话存在：临时附件按会话归档，删除会话时可一并清理
  if (!sessionId.value) {
    try {
      const s = await api.createSession(moduleKey.value)
      sessionId.value = s.id
      store.qaSessionId = s.id
    } catch {
      /* 会话创建失败不阻断提问，下方流式启动前会再尝试一次 */
    }
  }
  const files = picked.value.map((f) => f.name)
  if (!q) q = '请帮我审计这些文件'
  input.value = ''
  let docIds: string[] = []
  if (picked.value.length) {
    try {
      docIds = await uploadPicked()
      if (!docIds.length) {
        ElMessage.warning('附件上传失败，无法开始检测，请重试')
      }
    } catch (e) {
      ElMessage.error(`上传失败：${(e as Error).message}`)
    }
  }
  if (docIds.length) {
    q = `${q}\n\n待检测的文档 ID：${docIds.join('、')}`
  }
  await runAgent(q, files)
}

// 就地重试：复用原问题（含临时文档 ID）重新生成该轮回答
function retryMessage(idx: number) {
  if (sending.value) return
  const m = messages.value[idx] as AssistantMsg
  const q = m.result?.question
  if (!q) return
  const prev = idx > 0 ? messages.value[idx - 1] : null
  const files = prev && prev.role === 'user' ? prev.files : undefined
  const start = prev && prev.role === 'user' ? idx - 1 : idx
  messages.value.splice(start)
  void runAgent(q, files)
}

// 点赞/点踩，再次点击同一项可取消
async function rate(idx: number, value: 'good' | 'bad') {
  const m = messages.value[idx] as AssistantMsg
  const r = m.result
  if (!r?.id) return
  const prev = r.feedback
  r.feedback = prev === value ? '' : value
  try {
    await api.feedbackQA(r.id, r.feedback || '')
  } catch {
    r.feedback = prev
    ElMessage.error('反馈提交失败')
  }
}

async function runAgent(q: string, files?: string[]) {
  messages.value.push({ role: 'user', text: q, files })
  const partial: QAResult = {
    id: '', question: q, answer: { conclusion: '', basis: [] }, citations: [],
    createdAt: '', recallMode: '混合', feedback: null, route: 'agent', actions: [],
    files,
  }
  messages.value.push({ role: 'assistant', streaming: true, result: partial })
  const idx = messages.value.length - 1
  sending.value = true
  currentRunId.value = ''
  let started = false
  const controller = new AbortController()
  abortController.value = controller
  scrollToEnd()
  try {
    if (!sessionId.value) {
      const s = await api.createSession(moduleKey.value)
      sessionId.value = s.id
      store.qaSessionId = s.id
    }
    await api.streamAgentQuestion(q, '混合', sessionId.value, {
      onStart: (rid, qaId) => {
        started = true
        currentRunId.value = rid
        const m = messages.value[idx] as AssistantMsg
        if (m.result && qaId && !m.result.id) m.result.id = qaId
        if (m.result) m.result.status = 'running'
      },
      onStep: (action) => {
        const m = messages.value[idx] as AssistantMsg
        if (m.streaming && m.result) {
          m.result.actions = [...(m.result.actions || []), action]
        }
        const data = ((action.detail as { result?: Record<string, unknown> } | undefined)
          ?.result) || {}
        if (action.tool === 'detect_conflict' && action.ok && data.taskId) {
          m.conflict = {
            taskId: String(data.taskId), conflictCount: Number(data.conflictCount || 0),
            files: Number(data.files || 0), units: Number(data.units || 0),
            clauses: Number(data.clauses || 0), judgedPairs: Number(data.judgedPairs || 0),
            topics: (data.topics as string[]) || [],
          }
          m.conflictStage = t('assistant.finalizing')
        }
        if (action.tool === 'get_conflict_result' && action.ok && data.conflicts) {
          const c = m.conflict || { taskId: String(data.taskId || ''), conflictCount: Number(data.conflictCount || 0) }
          c.conflicts = data.conflicts as ConflictRow[]
          c.conflictCount = Number(data.conflictCount ?? c.conflicts.length)
          m.conflict = c
        }
        if (action.tool === 'audit_contract' && action.ok && data.taskId) {
          m.contract = {
            taskId: String(data.taskId), contractType: String(data.contractType || ''),
            mainContract: String(data.mainContract || ''),
            totalIssues: Number(data.totalIssues || 0), issueCount: Number(data.issueCount || 0),
            reconCount: Number(data.reconCount || 0), issues: [], reconciliation: [],
          }
          m.contractStage = t('assistant.finalizing')
        }
        if (action.tool === 'get_contract_result' && action.ok && data.issues) {
          m.contract = data as unknown as ContractDetail
        }
        if (action.tool === 'audit_workpaper' && action.ok && data.taskId) {
          m.workpaper = {
            taskId: String(data.taskId),
            unitCount: Number(data.unitCount || 0),
            workpaperCount: Number(data.workpaperCount || 0),
            totalIssues: Number(data.totalIssues || 0),
            issueCount: Number(data.issueCount || 0),
            reconCount: Number(data.reconCount || 0),
            issues: [], reconciliation: [],
          }
          m.workpaperStage = t('assistant.finalizing')
        }
        if (action.tool === 'get_workpaper_result' && action.ok && data.issues) {
          m.workpaper = data as unknown as WorkpaperDetail
        }
        scrollToEnd()
      },
      onConflictProgress: () => {
        // 内部解析/比对阶段不向用户展示，只保留一个稳定的通用状态。
        const m = messages.value[idx] as AssistantMsg
        m.conflictStage = t('assistant.analyzing')
        scrollToEnd()
      },
      onContractProgress: () => {
        const m = messages.value[idx] as AssistantMsg
        m.contractStage = t('assistant.analyzing')
        scrollToEnd()
      },
      onWorkpaperProgress: () => {
        const m = messages.value[idx] as AssistantMsg
        m.workpaperStage = t('assistant.analyzing')
        scrollToEnd()
      },
      onConfirm: (payload) => {
        const m = messages.value[idx] as AssistantMsg
        m.confirm = payload
        scrollToEnd()
      },
      onProgress: (payload) => {
        const m = messages.value[idx] as AssistantMsg
        const list = m.progress || (m.progress = [])
        const at = list.findIndex((x) => x.taskId === payload.taskId)
        if (at >= 0) list[at] = payload
        else list.push(payload)
        scrollToEnd()
      },
      onAnswerDelta: (text) => {
        const m = messages.value[idx] as AssistantMsg
        m.answerText = (m.answerText || '') + text
        if (m.result) m.result.answer = { ...m.result.answer, markdown: m.answerText }
        scrollToEnd()
      },
      onDone: (result) => {
        const prev = messages.value[idx] as AssistantMsg
        if (prev.conflict) result.conflict = prev.conflict
        if (prev.contract) result.contract = prev.contract
        if (prev.workpaper) result.workpaper = prev.workpaper
        result.status = 'done'
        messages.value[idx] = { role: 'assistant', result, answerText: '', progress: prev.progress,
                                conflict: prev.conflict, conflictStage: prev.conflictStage,
                                contract: prev.contract, contractStage: prev.contractStage,
                                workpaper: prev.workpaper, workpaperStage: prev.workpaperStage }
        void loadSessions()
        scrollToEnd()
      },
      onStopped: () => {
        const m = messages.value[idx] as AssistantMsg
        if (m.streaming && m.result) {
          messages.value[idx] = {
            role: 'assistant',
            result: { ...m.result, status: 'stopped',
              answer: { conclusion: '已停止，未生成完整回答。', basis: [], notice: '已停止运行。' } },
          }
        }
      },
      onError: (msg) => {
        messages.value[idx] = { role: 'assistant', error: msg }
      },
    }, controller.signal, moduleKey.value)
  } catch (e) {
    if ((e as Error).name === 'AbortError') {
      const m = messages.value[idx] as AssistantMsg
      if (m.streaming && m.result) {
        messages.value[idx] = {
          role: 'assistant',
          result: { ...m.result, status: 'stopped',
            answer: { conclusion: '已停止，未生成完整回答。', basis: [], notice: '已停止运行。' } },
        }
      }
    } else if (!started) {
      // SSE 尚未连上就失败（网络/代理/HTTP 错误）→ 回退到同步问答。
      // 若已 started，后端任务已启动，不能重复提问；保留 running 态，
      // 切换页面回来时由 onActivated 从会话历史同步最终结果。
      try {
        const result = await api.askQuestion(q, '混合', sessionId.value)
        messages.value[idx] = { role: 'assistant', result }
        await loadSessions()
      } catch (err) {
        messages.value[idx] = { role: 'assistant', error: `提问失败：${(err as Error).message}` }
      }
    }
  } finally {
    sending.value = false
    abortController.value = null
    scrollToEnd()
  }
}

function stopStream() {
  if (currentRunId.value) void api.stopAgent(currentRunId.value)
  abortController.value?.abort()
}

async function decideConfirm(idx: number, decision: 'approve' | 'reject') {
  const m = messages.value[idx] as AssistantMsg
  const cid = m.confirm?.confirmId
  if (!cid) return
  m.confirm = undefined
  try {
    await api.decideConfirm(cid, decision)
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

function openDoc(c: QACitation) {
  void router.push(`/document/${c.docId}?chunk=${c.chunkId}`)
}

function openCitation(msgIdx: number, n: number) {
  const m = messages.value[msgIdx] as AssistantMsg
  const c = m.result?.citations?.[n]
  if (c) openDoc(c)
}

function exportConflict(taskId: string) {
  const a = document.createElement('a')
  a.href = api.conflictExportUrl(taskId)
  a.download = `conflict-report-${taskId}.md`
  a.click()
}

function exportContract(taskId: string) {
  const a = document.createElement('a')
  a.href = api.contractExportUrl(taskId)
  a.download = `contract-report-${taskId}.md`
  a.click()
}

function exportWorkpaper(taskId: string) {
  const a = document.createElement('a')
  a.href = api.workpaperExportUrl(taskId)
  a.download = `workpaper-report-${taskId}.md`
  a.click()
}

function levelClass(level: string) {
  if (level === '证据支持的审计问题' || level === '证据支持的底稿缺陷') return 'lv-high'
  if (level === '待核实异常') return 'lv-mid'
  return 'lv-low'
}

function assessClass(result: string) {
  if (result === '符合') return 'lv-low'
  if (result === '不符合') return 'lv-high'
  return 'lv-mid'
}

let runningTimer = 0

// 会话里存在 running 占位（例如刷新页面、或 SSE 被中断）时，
// 定时从后端同步，直到该消息变成最终结果。
async function syncRunning() {
  if (sending.value || !sessionId.value) return
  const hasRunning = messages.value.some(
    (m) => m.role === 'assistant' && m.result?.status === 'running',
  )
  if (!hasRunning) {
    if (runningTimer) { window.clearInterval(runningTimer); runningTimer = 0 }
    return
  }
  try {
    messages.value = rowsToMessages(await api.getSession(sessionId.value))
    scrollToEnd()
  } catch {
    /* 同步失败下次再试 */
  }
}

function ensureRunningSync() {
  if (runningTimer) return
  runningTimer = window.setInterval(() => void syncRunning(), 3000)
}

onMounted(async () => {
  await loadSessions()
  store.setQaSwitchHandler(moduleKey.value, (id: string) => void switchSession(id))
  const first = store.qaSessions[0]
  if (first) await switchSession(first.id)
  else await newSession()
  ensureRunningSync()
})

// 从别的页面切回来时立即同步一次，并保证轮询在运行（覆盖刷新/长时间离开）。
onActivated(() => { void syncRunning(); ensureRunningSync() })

onUnmounted(() => {
  store.setQaSwitchHandler(moduleKey.value, null)
  if (runningTimer) { window.clearInterval(runningTimer); runningTimer = 0 }
})
</script>

<style scoped>
.qa-shell { display: flex; flex-direction: column; height: 100%; max-width: 860px; margin: 0 auto; }
.qa-shell.is-empty .qa-list { justify-content: center; }

.qa-list { flex: 1; overflow-y: auto; padding: 14px 2px 16px; display: flex; flex-direction: column; gap: 14px; }

.qa-empty { text-align: center; padding: 8px 0 28px; }
.qa-empty-hint { font-size: 14px; color: var(--sub); margin-bottom: 18px; }
.qa-suggests { display: flex; flex-wrap: wrap; gap: 9px; justify-content: center; }
.qa-suggest { font-size: 13px; color: var(--primary-dark); background: var(--surface); border: 1px solid #d6e2fd; border-radius: 18px; padding: 7px 15px; cursor: pointer; transition: all .15s; }
.qa-suggest:hover { background: var(--primary-soft); border-color: var(--primary); }
.qa-msg { display: flex; }
.qa-msg.user { justify-content: flex-end; }
.qa-msg.assistant { justify-content: flex-start; }

.bubble { max-width: 78%; padding: 12px 15px; border-radius: 12px; line-height: 1.85; font-size: 14px; word-break: break-word; }
.bubble.user { background: var(--primary); color: #fff; border-bottom-right-radius: 3px; }
.bubble.bot { background: var(--surface); border: 1px solid var(--border); border-bottom-left-radius: 3px; }
.bubble.error { color: #d35b52; border-color: #f3c9c6; }

.bubble.loading, .bubble-inline-loading { color: var(--muted); display: inline-flex; align-items: center; gap: 5px; font-size: 13px; }
.bubble-inline-loading { margin-bottom: 4px; }
.dot { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); animation: blink 1.2s infinite both; }
.dot:nth-child(2) { animation-delay: .2s; }
.dot:nth-child(3) { animation-delay: .4s; }
@keyframes blink { 0%, 80%, 100% { opacity: .25; } 40% { opacity: 1; } }

.qa-conclusion { white-space: pre-wrap; }
.qa-markdown { line-height: 1.8; }
.typing-cursor { display: inline-block; width: 2px; height: 13px; margin-left: 2px; vertical-align: -2px; background: var(--primary); animation: caret .9s steps(1) infinite; }
@keyframes caret { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0; } }

.qa-actions { margin-top: 10px; display: flex; flex-direction: column; gap: 5px; }
.qa-action { font-size: 12px; color: var(--sub); border: 1px solid var(--border); border-radius: 7px; background: var(--surface); }
.qa-action summary { display: flex; align-items: center; gap: 6px; padding: 5px 9px; cursor: pointer; list-style: none; }
.qa-action summary::-webkit-details-marker { display: none; }
.qa-action-dot { width: 6px; height: 6px; border-radius: 50%; background: #3a9d6e; }
.qa-action-dot.failed { background: #d35b52; }
.qa-action-dot.writable { background: #d98a2b; }
.qa-action-kind { font-size: 10px; color: var(--muted); border: 1px solid var(--border); border-radius: 4px; padding: 0 5px; }
.qa-action-ms { margin-left: auto; color: var(--muted); font-size: 11px; }

.qa-progress { margin-top: 8px; display: flex; flex-direction: column; gap: 3px; }
.qa-progress-row { display: flex; justify-content: space-between; gap: 10px; font-size: 11.5px; color: var(--sub); }

.qa-confirm { margin-top: 10px; border: 1px solid #d6e2fd; background: var(--primary-soft); border-radius: 8px; padding: 9px 11px; }
.qa-confirm.high { border-color: #f0c4bf; background: #fdf3f2; }
.qa-confirm-head { display: flex; align-items: center; gap: 8px; font-size: 12.5px; font-weight: 600; }
.qa-confirm-tag { font-size: 11px; font-weight: 500; color: var(--primary-dark); border: 1px solid #d6e2fd; border-radius: 5px; padding: 1px 6px; }
.qa-confirm.high .qa-confirm-tag { color: #c15b52; border-color: #f0c4bf; }
.qa-confirm-args { margin: 8px 0 0; max-height: 150px; overflow: auto; font-size: 11px; line-height: 1.6; white-space: pre-wrap; word-break: break-all; color: var(--sub); }
.qa-confirm-warn { margin-top: 7px; font-size: 11.5px; color: #c15b52; }
.qa-confirm-btns { margin-top: 9px; display: flex; gap: 8px; }
.qa-action-detail { margin: 0; padding: 8px 10px; border-top: 1px solid var(--border); max-height: 220px; overflow: auto; font-size: 11px; line-height: 1.6; color: var(--sub); white-space: pre-wrap; }

.qa-basis { margin: 10px 0 0; padding-left: 18px; color: var(--sub); font-size: 13px; }
.qa-basis li { margin: 3px 0; }

.qa-cites { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.qa-cites-label { font-size: 12px; color: var(--muted); }
.cite-chip { font-size: 12px; color: var(--primary-dark); background: var(--primary-soft); border: 1px solid #d6e2fd; border-radius: 7px; padding: 3px 9px; cursor: pointer; max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cite-chip:hover { background: #dde7fe; }

.qa-feedback { margin-top: 10px; display: flex; align-items: center; gap: 3px; border-top: 1px solid var(--border); padding-top: 7px; }
.fb-btn { font-size: 13px; line-height: 1; color: var(--muted); background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 3px 7px; cursor: pointer; transition: all .15s; }
.fb-btn:hover { color: var(--primary-dark); background: var(--primary-soft); }
.fb-btn.active { color: var(--primary-dark); background: var(--primary-soft); border-color: #d6e2fd; }
.fb-retry { font-size: 12px; margin-left: 2px; }

.qa-foot { margin-top: 12px; display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.qa-fb { display: flex; gap: 2px; }

.qa-retrieval { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.qa-retrieval-label { font-size: 11px; color: var(--muted); }
.qa-retrieval-tag { font-size: 11px; color: var(--primary-dark); background: var(--primary-soft); border: 1px solid #d6e2fd; border-radius: 6px; padding: 2px 8px; }

.qa-input { display: flex; gap: 10px; align-items: flex-end; padding: 12px 2px 4px; border-top: 1px solid var(--border); }
.qa-input .el-button { flex: 0 0 auto; }
.qa-input .el-upload { flex: 0 0 auto; }

.user-files { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 5px; }
.user-file-chip { font-size: 11px; background: rgba(255, 255, 255, 0.18); border-radius: 5px; padding: 2px 8px; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.qa-picked { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 2px 0; }

.contract-report { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 10px; }
.contract-report-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; font-size: 14px; font-weight: 600; color: var(--primary, #2f6690); }
.contract-sub { font-size: 12px; color: var(--muted); margin: 6px 0; }
.contract-section { font-size: 13px; font-weight: 600; color: var(--sub); margin: 12px 0 4px; }
.contract-issue { border-top: 1px solid var(--border); padding: 9px 0; }
.contract-issue-head { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.contract-no { font-weight: 600; }
.contract-title { font-size: 13px; font-weight: 600; }
.contract-level { font-size: 11px; padding: 1px 7px; border-radius: 9px; }
.contract-level.lv-high { background: #fbe9e7; color: #b23b30; }
.contract-level.lv-mid { background: #fdf1d9; color: #9a6a12; }
.contract-level.lv-low { background: #e8f3ea; color: #2f7a46; }
.contract-kv { font-size: 12.5px; line-height: 1.65; margin-top: 4px; }
.contract-kv b { color: var(--sub); font-weight: 600; }
.contract-quote { background: var(--surface-soft); border-left: 3px solid var(--primary-light, #9db8f0); border-radius: 5px; padding: 6px 10px; margin: 6px 0; font-size: 12px; line-height: 1.6; }
.conflict-report { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 10px; }
.conflict-report-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; font-size: 14px; font-weight: 600; color: #cf4d45; }
.conflict-meta { font-size: 12px; color: var(--muted); margin: 6px 0 10px; }
.conflict-row { border-top: 1px solid var(--border); padding: 10px 0; }
.conflict-row-head { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.conflict-no { font-weight: 600; }
.conflict-topic { font-size: 13px; color: var(--sub); }
.conflict-exp { font-size: 13px; line-height: 1.7; margin: 8px 0; }
.conflict-quote { background: var(--surface-soft); border-left: 3px solid var(--primary-light, #9db8f0); border-radius: 5px; padding: 7px 10px; margin-bottom: 7px; font-size: 12.5px; line-height: 1.6; }
.conflict-quote b { display: block; font-size: 11.5px; color: var(--sub); margin-bottom: 3px; word-break: break-all; }
</style>
