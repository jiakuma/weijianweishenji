<template>
  <div class="qa-shell" :class="{ 'is-empty': !messages.length }">
    <div ref="listEl" class="qa-list">
      <div v-if="!messages.length" class="welcome">
        <span class="welcome-icon"><el-icon><component :is="cfg.icon" /></el-icon></span>
        <h1 class="welcome-title">你好，我能帮你什么吗？</h1>
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
              @cite="(n: number) => openCitation(i, n + 1)"
              :cite-kinds="citeKinds(m)"
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
              <span>文档审核报告</span>
              <el-button size="small" plain @click="exportWorkpaper(m.workpaper.taskId)">{{ t('common.export') }}</el-button>
            </div>
            <div class="contract-sub">被审计单位 {{ m.workpaper.unitCount || 0 }} 家 · 审计底稿 {{ m.workpaper.workpaperCount || 0 }} 份</div>
            <div class="contract-section">一、详细审核结果</div>
            <div v-for="it in workpaperRows(m.workpaper)" :key="it.key" class="wp-item">
              <div class="wp-item-head">{{ it.no }}. {{ it.topic }}</div>
              <div class="wp-kv">对应文件：{{ it.file }}</div>
              <div class="wp-kv" :class="it.resultClass">审核结果：{{ it.resultText }}</div>
              <div class="wp-kv">{{ it.reasonLabel }}{{ it.reason }}</div>
            </div>
            <div v-if="!m.workpaper.summary?.total" class="qa-conclusion">未能从上位文件中提取到可核对的审计事项</div>
            <div class="contract-section">二、总体审核结果</div>
            <div class="wp-kv">审核要点总数：{{ m.workpaper.summary?.total || 0 }} 项</div>
            <div class="wp-kv wp-pass">✅ 审核通过：{{ m.workpaper.summary?.pass || 0 }} 项</div>
            <div class="wp-kv wp-fail">❌ 审核不通过：{{ m.workpaper.summary?.fail || 0 }} 项</div>
            <div class="wp-kv">总体审核结果：{{ m.workpaper.summary?.conclusion }}</div>
            <div v-if="m.workpaper.summary?.opinion" class="wp-kv">总体审核意见：{{ m.workpaper.summary.opinion }}</div>
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
    <div class="qa-composer">
      <div class="composer-card">
        <el-input
          v-model="input"
          class="composer-textarea"
          type="textarea"
          :rows="2"
          resize="none"
          :placeholder="t('assistant.placeholder')"
          @keydown.enter.exact.prevent="send"
        />
        <div class="composer-bar">
          <el-upload :auto-upload="false" :show-file-list="false" multiple :accept="ACCEPT" :on-change="onPick">
            <button class="composer-attach" :disabled="sending" title="上传文件">
              <el-icon><Paperclip /></el-icon>
              <span>附件</span>
            </button>
          </el-upload>
          <span class="composer-hint">Enter 发送 · Shift + Enter 换行</span>
          <el-button v-if="sending" class="composer-send" type="danger" plain @click="stopStream">{{ t('assistant.stop') }}</el-button>
          <el-button v-else class="composer-send" type="primary" :disabled="!input.trim() && !picked.length" :loading="uploading" @click="send">
            <el-icon><ArrowRight /></el-icon>
            <span>{{ t('assistant.send') }}</span>
          </el-button>
        </div>
      </div>
    </div>

    <el-drawer v-model="citeDrawerVisible" title="引用详情" size="560px" append-to-body>
      <div v-if="activeCitation" class="cite-detail">
        <template v-if="activeCitation.kind === 'issue'">
          <div class="cite-detail-title">{{ activeCitation.catL4 || activeCitation.category || '历史审计问题' }}</div>
          <div class="cite-detail-meta">
            {{ activeCitation.sourceYear }} 年度 · {{ activeCitation.auditSubject || '未标注主体' }} · {{ activeCitation.auditProject || '未标注项目' }}
          </div>
          <div class="cite-detail-section">
            <div class="cite-detail-label">分类链</div>
            <div class="cite-detail-cats">
              <el-tag v-for="(cat, ci) in issueCats(activeCitation)" :key="`${ci}-${cat}`" size="small" effect="plain" round>
                {{ ci + 1 }}级 · {{ cat }}
              </el-tag>
              <span v-if="!issueCats(activeCitation).length">未分类</span>
            </div>
          </div>
          <div v-if="activeCitation.manifestation" class="cite-detail-section">
            <div class="cite-detail-label">问题表现</div>
            <div class="cite-detail-text">{{ activeCitation.manifestation }}</div>
          </div>
          <div v-if="activeCitation.problemDesc" class="cite-detail-section">
            <div class="cite-detail-label">问题描述</div>
            <div class="cite-detail-text">{{ activeCitation.problemDesc }}</div>
          </div>
          <div v-if="activeCitation.legalBasis" class="cite-detail-section">
            <div class="cite-detail-label">定性依据</div>
            <div class="cite-detail-text">{{ activeCitation.legalBasis }}</div>
          </div>
          <div v-if="activeCitation.remediation" class="cite-detail-section">
            <div class="cite-detail-label">整改措施</div>
            <div class="cite-detail-text">{{ activeCitation.remediation }}</div>
          </div>
          <div v-if="activeCitation.sourceFile" class="cite-detail-section cite-detail-source">
            <span class="cite-detail-label">来源文件</span>{{ activeCitation.sourceFile }}
          </div>
          <div class="cite-detail-notice">历史问题仅供检索参考，定性须由审计人员复核。</div>
          <div class="cite-detail-actions">
            <el-button type="primary" @click="openIssueLibrary">打开历史问题库</el-button>
          </div>
        </template>
        <template v-else>
          <div class="cite-detail-title">《{{ activeCitation.docTitle }}》</div>
          <div class="cite-detail-meta">第 {{ activeCitation.page }} 页 · {{ activeCitation.section || '正文' }}</div>
          <div class="cite-detail-section">
            <div class="cite-detail-label">引用原文</div>
            <div class="cite-detail-quote">{{ activeCitation.quote || '未记录原文片段' }}</div>
          </div>
          <div class="cite-detail-actions">
            <el-button type="primary" @click="openCitationDoc">打开原文</el-button>
          </div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onActivated, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Paperclip, ChatDotRound, Connection, Tickets, Document, ArrowRight } from '@element-plus/icons-vue'
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
const moduleConfig: Record<string, { title: string; icon: typeof ChatDotRound }> = {
  assistant: { title: '审计工作助手', icon: ChatDotRound },
  conflict: { title: '制度互斥检测', icon: Connection },
  contract: { title: '合同审计', icon: Tickets },
  workpaper: { title: '底稿审核', icon: Document },
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
  // keep-alive 切回时 DOM 可能要跨帧才完成布局，双 nextTick + rAF 保证滚到底。
  nextTick(() => nextTick(() => {
    requestAnimationFrame(() => {
      const el = listEl.value
      if (el) el.scrollTop = el.scrollHeight
    })
  }))
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

function lastSessionKey(): string {
  return `audit-last-session-${moduleKey.value}`
}

function rememberSession(id: string) {
  try { localStorage.setItem(lastSessionKey(), id) } catch { /* 隐私模式下忽略 */ }
}

async function switchSession(id: string) {
  sessionId.value = id
  store.qaSessionId = id
  rememberSession(id)
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
  store.qaSessionId = id
  rememberSession(id)
  messages.value = []
}

// 每次进入本模块都恢复到「最后一次对话的会话」：
// 优先用本模块记住的会话，失效则取后端按 updated_at 倒序返回的第一条。
// 同时把全局 qaSessionId 校正回来，避免被其他模块覆盖导致侧边栏高亮丢失。
async function recoverSession() {
  store.setQaSwitchHandler(moduleKey.value, (id: string) => void switchSession(id))
  try {
    await loadSessions()
  } catch { /* 列表加载失败不阻断问答 */ }
  let remembered = ''
  try { remembered = localStorage.getItem(lastSessionKey()) || '' } catch { /* ignore */ }
  const target = store.qaSessions.find((s) => s.id === remembered) || store.qaSessions[0]
  if (target) {
    if (sessionId.value !== target.id) await switchSession(target.id)
    else {
      store.qaSessionId = target.id
      // 会话没变时 switchSession 不会执行，这里补一次滚动。
      scrollToEnd()
    }
  } else {
    await newSession()
  }
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
      rememberSession(s.id)
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
      rememberSession(s.id)
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
          // 后端已改为输出 items/summary 报告结构，整体透传避免丢字段。
          m.workpaper = { ...data } as unknown as WorkpaperDetail
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
  // "打开原文"直接进文档页的原始文件 tab，与资料库/导入页打开文档保持一致；
  // 不再带 chunk 参数（带了会切到「知识切片」tab，与按钮语义不符）。
  void router.push(`/document/${c.docId}`)
}

const citeDrawerVisible = ref(false)
const activeCitation = ref<QACitation | null>(null)

/** 1-based 角标号 → 引用类型，交给 MarkdownView 区分历史问题/制度角标样式。 */
function citeKinds(m: Msg): Record<number, string> {
  const kinds: Record<number, string> = {}
  const list = (m as AssistantMsg).result?.citations || []
  list.forEach((c, i) => { kinds[c.refNo ?? i + 1] = c.kind || 'doc' })
  return kinds
}

function issueCats(c: QACitation): string[] {
  return [c.catL1, c.catL2, c.catL3, c.catL4].filter((x): x is string => Boolean(x))
}

/** 所有引用（正文角标与底部 chip）统一先打开右侧抽屉，再决定是否跳转页面。 */
function openCitation(msgIdx: number, n: number) {
  const m = messages.value[msgIdx] as AssistantMsg
  const list = m.result?.citations || []
  const idx = list.findIndex((item, i) => (item.refNo ?? i + 1) === n)
  const c = idx >= 0 ? list[idx] : list[n - 1]
  if (!c) return
  activeCitation.value = c
  citeDrawerVisible.value = true
}

function openCitationDoc() {
  const c = activeCitation.value
  if (!c) return
  citeDrawerVisible.value = false
  openDoc(c)
}

function openIssueLibrary() {
  const id = activeCitation.value?.issueId
  citeDrawerVisible.value = false
  void router.push(id ? `/issues?id=${encodeURIComponent(id)}` : '/issues')
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

interface WorkpaperRow {
  key: string
  no: string | number
  topic: string
  file: string
  resultText: string
  resultClass: string
  reasonLabel: string
  reason: string
}

type WorkpaperRowSource =
  | NonNullable<WorkpaperDetail['items']>[number]
  | NonNullable<WorkpaperDetail['assessments']>[number]

/**
 * 把新版 items 与旧版 assessments 归一为同一行结构，供报告卡片直接渲染。
 * 结果文案与配色统一按“通过 / 不通过 / 无法判断”三态呈现。
 */
function workpaperRows(wp: WorkpaperDetail | undefined): WorkpaperRow[] {
  const source: WorkpaperRowSource[] = wp?.items?.length ? wp.items : (wp?.assessments || [])
  return source.map((raw, i) => {
    const it = raw as WorkpaperRowSource
    const topic = 'topic' in it ? (it.topic || '') : (it.requirement || '')
    const file = 'file' in it ? (it.file || '') : (it.source || '')
    const no = 'index' in it ? (it.index ?? i + 1) : i + 1
    const result = String(it.result || '')
    const failed = result.includes('不通过') || result.includes('不符合')
    const passed = !failed && (result.includes('通过') || result.includes('符合'))
    return {
      key: `${no}-${topic}-${i}`,
      no,
      topic,
      file,
      resultText: failed ? '❌ 审核不通过' : passed ? '✅ 审核通过' : '⚠️ 无法判断',
      resultClass: failed ? 'wp-fail' : passed ? 'wp-pass' : 'wp-unknown',
      reasonLabel: failed ? '不通过原因：' : passed ? '通过原因：' : '说明：',
      reason: it.reason || '',
    }
  })
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

// 防止 onMounted 与首次 onActivated 重叠导致重复恢复/重复请求。
let recovering = false
async function recoverOnce() {
  if (recovering) return
  recovering = true
  try { await recoverSession() } finally { recovering = false }
}

onMounted(async () => {
  await recoverOnce()
  ensureRunningSync()
})

// 从别的模块/页面切回来时，恢复到本模块最后一次对话的会话、校正高亮，
// 并滚动到最后一条消息，避免长对话每次都要手动往下拉。
onActivated(() => {
  void recoverOnce().then(() => scrollToEnd())
  void syncRunning()
  ensureRunningSync()
})

onUnmounted(() => {
  store.setQaSwitchHandler(moduleKey.value, null)
  if (runningTimer) { window.clearInterval(runningTimer); runningTimer = 0 }
})
</script>

<style scoped>
.qa-shell { display: flex; flex-direction: column; height: 100%; max-width: 900px; margin: 0 auto; padding: 8px 32px 0; }

.qa-list { flex: 1; min-height: 0; overflow-y: auto; padding: 18px 4px 20px; display: flex; flex-direction: column; gap: 18px; }
.qa-list::-webkit-scrollbar, .qa-rail-list::-webkit-scrollbar { width: 7px; }
.qa-list::-webkit-scrollbar-thumb, .qa-rail-list::-webkit-scrollbar-thumb { background: #d9e0ec; border-radius: 8px; }
.qa-list::-webkit-scrollbar-thumb:hover, .qa-rail-list::-webkit-scrollbar-thumb:hover { background: #c4cddd; }

.qa-msg { display: flex; }
.qa-msg.user { justify-content: flex-end; }
.qa-msg.assistant { justify-content: flex-start; }

.bubble { max-width: 80%; padding: 13px 17px; border-radius: 15px; line-height: 1.85; font-size: 14px; word-break: break-word; box-shadow: var(--shadow-sm); }
.bubble.user {
  background: linear-gradient(135deg, var(--primary), #5666dd); color: #fff;
  border-bottom-right-radius: 4px; box-shadow: 0 4px 14px rgba(74, 91, 214, .2);
}
.bubble.bot { background: var(--surface); border: 1px solid var(--border); border-bottom-left-radius: 4px; }
.bubble.error { color: #d35b52; border-color: #f3c9c6; }

/* 空状态：欢迎语与输入框一起垂直居中（DeepSeek 风格） */
.qa-shell.is-empty { justify-content: center; }
.qa-shell.is-empty .qa-list { flex: 0 0 auto; overflow: visible; padding: 0; }
.qa-shell.is-empty .qa-composer { width: 100%; max-width: 660px; margin: 0 auto; padding-top: 26px; }

.welcome { display: flex; align-items: center; justify-content: center; gap: 12px; padding: 8px 0; }
.welcome-icon {
  width: 36px; height: 36px; flex: 0 0 36px; border-radius: 10px;
  display: grid; place-items: center; color: #fff;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  box-shadow: 0 5px 14px rgba(74, 91, 214, .24);
}
.welcome-icon .el-icon { font-size: 20px; }
.welcome-title { margin: 0; font-size: 26px; font-weight: 700; color: var(--text); letter-spacing: .3px; }

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

.cite-detail-title { font-size: 17px; font-weight: 650; color: var(--text); line-height: 1.5; }
.cite-detail-meta { margin-top: 6px; font-size: 12.5px; color: var(--muted); }
.cite-detail-section { margin-top: 18px; }
.cite-detail-label { display: block; margin-bottom: 5px; font-size: 12px; font-weight: 600; color: var(--sub); }
.cite-detail-cats { display: flex; flex-wrap: wrap; gap: 6px; }
.cite-detail-text { font-size: 13px; line-height: 1.8; white-space: pre-wrap; word-break: break-word; }
.cite-detail-quote { font-size: 13px; line-height: 1.8; white-space: pre-wrap; word-break: break-word; background: var(--surface-soft); border-left: 3px solid var(--primary-light, #9db8f0); border-radius: 5px; padding: 9px 12px; }
.cite-detail-source { font-size: 12.5px; color: var(--sub); word-break: break-all; }
.cite-detail-source .cite-detail-label { display: inline; margin-right: 7px; }
.cite-detail-notice { margin-top: 22px; padding: 9px 11px; font-size: 12px; color: #9a6a12; background: #fff8e8; border-radius: 6px; }
.cite-detail-actions { display: flex; justify-content: flex-end; margin-top: 20px; padding-top: 14px; border-top: 1px solid var(--border); }

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

.qa-composer { padding: 12px 0 6px; }
.composer-card {
  background: var(--surface); border: 1px solid var(--border-strong);
  border-radius: 16px; padding: 6px 8px 8px; box-shadow: var(--shadow-md);
  transition: border-color .18s, box-shadow .18s;
}
.composer-card:focus-within { border-color: var(--primary); box-shadow: 0 6px 28px rgba(74, 91, 214, .14); }
.composer-textarea :deep(.el-textarea__inner) {
  border: none; box-shadow: none !important; background: transparent;
  padding: 10px 12px 4px; font-size: 14px; line-height: 1.7; color: var(--text);
}
.composer-textarea :deep(.el-textarea__inner::placeholder) { color: var(--muted); }
.composer-bar { display: flex; align-items: center; gap: 10px; padding: 2px 6px 0; }
.composer-bar .el-upload { flex: 0 0 auto; }
.composer-attach {
  display: inline-flex; align-items: center; gap: 5px; height: 32px; padding: 0 11px;
  background: transparent; border: 1px solid var(--border); border-radius: 9px;
  color: var(--sub); font-size: 12.5px; cursor: pointer; transition: all .15s;
}
.composer-attach:hover { color: var(--primary-dark); border-color: var(--primary); background: var(--primary-hover); }
.composer-attach:disabled { opacity: .5; cursor: not-allowed; }
.composer-hint { font-size: 11.5px; color: var(--muted); }
.composer-send { min-width: 88px; height: 34px; border-radius: 9px; font-weight: 600; margin-left: auto; }
.composer-send .el-icon { margin-right: 4px; }

.user-files { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 5px; }
.user-file-chip { font-size: 11px; background: rgba(255, 255, 255, 0.18); border-radius: 5px; padding: 2px 8px; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.qa-picked { display: flex; flex-wrap: wrap; gap: 6px; padding: 4px 6px 8px; }

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
.wp-item { margin: 12px 0; }
.wp-item-head { font-weight: 600; }
.wp-kv { color: var(--sub); font-size: 13px; line-height: 1.7; margin-top: 3px; }
.wp-pass { color: #2e7d32; }
.wp-fail { color: #c62828; }
.wp-unknown { color: #8a8a8a; }
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
