<template>
  <div class="conflict-shell">
    <aside class="session-rail">
      <div class="rail-head">
        <span class="rail-title">会话记录</span>
        <el-button text size="small" :icon="Plus" @click="newSession" />
      </div>
      <div class="rail-list">
        <div v-if="!sessions.length" class="rail-empty">暂无会话</div>
        <div
          v-for="s in sessions"
          :key="s.id"
          class="rail-item"
          :class="{ 'is-active': s.id === currentSessionId }"
          @click="switchSession(s.id)"
        >
          <div class="rail-item-title">{{ s.title || '新会话' }}</div>
          <div class="rail-item-meta">{{ s.updated_at }} · {{ s.task_count }} 次检测</div>
          <el-button text size="small" class="rail-del" @click.stop="removeSession(s)">×</el-button>
        </div>
      </div>
    </aside>

    <div class="conflict-chat">
    <div class="page-head">
      <div class="conflict-head">
        <h2 class="page-title">制度互斥</h2>
        <el-tag size="small" :type="config?.mode === '未配置' ? 'warning' : 'success'">
          {{ config?.mode ?? '连接中' }}
        </el-tag>
      </div>
    </div>

    <div ref="stream" class="chat-stream">
      <div v-if="!visibleTasks.length" class="chat-welcome">
        <div class="welcome-mark">审</div>
        <div class="welcome-text">{{ sessions.length ? '上传制度文件，开始互斥检测' : '新建会话，开始互斥检测' }}</div>
      </div>

      <div v-for="t in visibleTasks" :key="t.task_id" class="turn">
        <div class="msg user">
          <div class="bubble user-bubble">
            <div v-for="name in fileNames(t)" :key="name" class="user-file">
              <el-icon><Document /></el-icon><span>{{ name }}</span>
            </div>
          </div>
        </div>

        <div class="msg assistant">
          <div class="assistant-mark">审</div>
          <div class="assistant-body">
            <div class="thought">
              <div class="thought-head" @click="toggleThought(t.task_id)">
                <el-icon class="thought-icon"><ChatLineRound /></el-icon>
                <span>{{ thoughtTitle(t) }}</span>
                <el-icon class="thought-arrow" :class="{ open: isThoughtOpen(t) }"><ArrowDown /></el-icon>
                <el-button text size="small" class="msg-del" :icon="Delete" @click.stop="removeTask(t)" />
              </div>
              <div v-if="isThoughtOpen(t)" class="thought-steps">
                <div v-for="(line, i) in thoughts(t)" :key="i" class="thought-step" :class="{ current: i === thoughts(t).length - 1 && t.status !== 'done' }">
                  <span class="step-dot">{{ i === thoughts(t).length - 1 && t.status === 'done' ? '✓' : i + 1 }}</span>
                  <span>{{ line }}</span>
                </div>
              </div>
            </div>

            <div v-if="t.status === 'error'" class="result-error">{{ t.error || '检测失败' }}</div>

            <template v-else-if="t.status === 'done'">
              <div class="result-line">
                <span :class="{ 'has-conflict': t.conflict_count > 0 }">
                  {{ t.conflict_count > 0 ? `检出 ${t.conflict_count} 处互斥条款` : '未发现互斥条款' }}
                </span>
              </div>
              <div class="result-actions">
                <el-button size="small" @click="toggleReport(t)">{{ expanded.has(t.task_id) ? '收起报告' : '查看报告' }}</el-button>
                <el-button size="small" type="primary" plain @click="exportReport(t)">导出报告</el-button>
              </div>

              <div v-if="expanded.has(t.task_id)" class="report">
                <div v-if="detailOf(t.task_id)?.error" class="result-error">{{ detailOf(t.task_id)?.error }}</div>
                <div v-if="detailOf(t.task_id)" class="report-summary">
                  <span>制度互斥检测报告</span>
                  <el-divider direction="vertical" />
                  <span>{{ t.created_at }}</span>
                  <el-divider direction="vertical" />
                  <span>{{ t.total }} 个文件</span>
                  <el-divider direction="vertical" />
                  <span :class="{ 'has-conflict': t.conflict_count > 0 }">检出互斥 {{ t.conflict_count }} 处</span>
                </div>
                <div v-if="detailOf(t.task_id) && !detailOf(t.task_id)!.conflicts.length" class="report-empty">在本次比对范围内未发现互斥条款</div>
                <div v-for="(c, i) in detailOf(t.task_id)?.conflicts ?? []" :key="i" class="report-item">
                  <div class="report-item-head">
                    <span class="report-no">{{ i + 1 }}</span>
                    <el-tag size="small" :type="sevType(c.severity)">{{ c.severity }}</el-tag>
                    <el-tag size="small">{{ c.conflict_type }}</el-tag>
                    <span class="report-topic">{{ c.topic }}</span>
                  </div>
                  <div class="report-exp">{{ c.explanation }}</div>
                  <div class="report-quote">
                    <div class="conflict-loc">甲 · {{ locOf(c, 'a') }}</div>
                    <div class="report-text">{{ c.quote_a }}</div>
                  </div>
                  <div class="report-quote">
                    <div class="conflict-loc">乙 · {{ locOf(c, 'b') }}</div>
                    <div class="report-text">{{ c.quote_b }}</div>
                  </div>
                </div>
                <div v-if="detailOf(t.task_id)" class="report-footnote">
                  本结果仅代表已精判的比对范围；冲突认定由大模型作出，重要事项请人工复核后使用。
                </div>
              </div>
            </template>

            <div v-else class="result-pending">正在分析…</div>
          </div>
        </div>
      </div>
    </div>

    <div class="composer">
      <el-upload
        :auto-upload="false"
        :show-file-list="false"
        multiple
        accept=".pdf,.docx,.doc,.txt,.md,.xlsx,.xls"
        :on-change="onChange"
      >
        <el-button circle text :icon="Paperclip" />
      </el-upload>
      <div class="composer-field">
        <div v-if="picked.length" class="picked-chips">
          <el-tag v-for="(f, i) in picked" :key="`${f.name}-${i}`" closable size="small" @close="picked.splice(i, 1)">
            {{ f.name }}
          </el-tag>
        </div>
        <span v-else class="composer-placeholder">上传制度文件开始检测</span>
      </div>
      <el-button circle type="primary" :icon="Promotion" :disabled="!picked.length" :loading="submitting" @click="start" />
    </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, type UploadProps } from 'element-plus'
import { ArrowDown, ChatLineRound, Delete, Document, Paperclip, Plus, Promotion } from '@element-plus/icons-vue'
import { api } from '@/api'
import type { ConflictConfig, ConflictItem, ConflictSession, ConflictTaskDetail, ConflictTaskItem } from '@/types'

type TagType = 'success' | 'primary' | 'warning' | 'danger' | 'info'

const config = ref<ConflictConfig | null>(null)
const tasks = ref<ConflictTaskItem[]>([])
const sessions = ref<ConflictSession[]>([])
const currentSessionId = ref('')
const picked = ref<File[]>([])
const submitting = ref(false)
const details = reactive<Record<string, ConflictTaskDetail>>({})
const expanded = reactive(new Set<string>())
const thoughtOverride = reactive(new Map<string, boolean>())
const stream = ref<HTMLElement | null>(null)
let timer: number | undefined

const currentIds = computed(() => {
  const s = sessions.value.find((x) => x.id === currentSessionId.value)
  return s ? s.task_ids : []
})
const visibleTasks = computed(() => {
  const ids = currentIds.value
  const inSession = tasks.value.filter((t) => ids.includes(t.task_id))
  return inSession.length ? inSession : (currentSessionId.value ? [] : tasks.value)
})
const activeTask = computed(() => visibleTasks.value.some((t) => t.status === 'pending' || t.status === 'running'))

const onChange: UploadProps['onChange'] = (file) => {
  const raw = file.raw
  if (!raw) return
  if (picked.value.some((f) => f.name === raw.name && f.size === raw.size)) return
  picked.value.push(raw)
}

async function loadSessions() {
  try {
    sessions.value = await api.conflictSessions()
    if (!currentSessionId.value && sessions.value.length) {
      currentSessionId.value = sessions.value[0].id
    }
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : String(e))
  }
}

async function loadTasks() {
  try {
    tasks.value = await api.conflictTasks()
    for (const id of expanded) {
      if (!details[id]) await loadDetail(id)
    }
    await scrollDown()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : String(e))
  }
}

async function newSession() {
  try {
    const s = await api.createConflictSession()
    await loadSessions()
    currentSessionId.value = s.id
    expanded.clear()
    await scrollDown()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : String(e))
  }
}

function switchSession(id: string) {
  if (id === currentSessionId.value) return
  currentSessionId.value = id
  expanded.clear()
  void scrollDown()
}

async function removeSession(s: ConflictSession) {
  try {
    await ElMessageBox.confirm(`删除会话「${s.title || '新会话'}」及其中的检测记录？`, '删除', { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.deleteConflictSession(s.id)
    for (const id of s.task_ids) delete details[id]
    if (currentSessionId.value === s.id) currentSessionId.value = ''
    await loadSessions()
    await loadTasks()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : String(e))
  }
}

async function loadDetail(id: string) {
  try {
    details[id] = await api.conflictTask(id)
  } catch {
    // 详情暂不可用时保留已有结果摘要
  }
}

async function scrollDown() {
  await nextTick()
  if (stream.value) stream.value.scrollTop = stream.value.scrollHeight
}

async function start() {
  if (!picked.value.length) return
  submitting.value = true
  try {
    await api.conflictAnalyze(picked.value, currentSessionId.value || undefined)
    picked.value = []
    await loadSessions()
    await loadTasks()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : String(e))
  } finally {
    submitting.value = false
  }
}

function detailOf(id: string): ConflictTaskDetail | undefined {
  return details[id]
}

async function toggleReport(t: ConflictTaskItem) {
  if (expanded.has(t.task_id)) {
    expanded.delete(t.task_id)
    return
  }
  expanded.add(t.task_id)
  if (!details[t.task_id]) await loadDetail(t.task_id)
}

function toggleThought(id: string) {
  thoughtOverride.set(id, !isThoughtOpenById(id))
}

function isThoughtOpenById(id: string): boolean {
  if (thoughtOverride.has(id)) return thoughtOverride.get(id) as boolean
  const t = tasks.value.find((x) => x.task_id === id)
  return t ? t.status === 'running' || t.status === 'pending' : false
}

function isThoughtOpen(t: ConflictTaskItem): boolean {
  return isThoughtOpenById(t.task_id)
}

const ORDER = ['解析中', '归一化中', '召回候选', '制度对粗判', '比对中', '完成']

function thoughts(t: ConflictTaskItem): string[] {
  const lines = [`收到 ${t.total} 个文件，开始处理`]
  if (t.status === 'error') return [...lines, '检测中断']
  if (t.status === 'done') {
    return [
      ...lines,
      `解析文件 ${t.done}/${t.total}`,
      '拆分制度单元与条款',
      '召回候选条款',
      '粗判制度管辖范围',
      `精判比对 ${t.compare_done || t.compare_total} 对条款`,
      '分析完成',
    ]
  }
  const idx = t.stage === '排队中' ? 0 : Math.max(0, ORDER.indexOf(t.stage))
  if (idx >= 0) lines.push(`解析文件 ${t.done}/${t.total}`)
  if (idx >= 1) lines.push('拆分制度单元与条款')
  if (idx >= 2) lines.push('召回候选条款')
  if (idx >= 3) lines.push('粗判制度管辖范围')
  if (idx >= 4) lines.push(`精判比对 ${t.compare_done}/${t.compare_total} 对条款`)
  return lines
}

function thoughtTitle(t: ConflictTaskItem): string {
  if (t.status === 'done') return '已完成分析'
  if (t.status === 'error') return '分析失败'
  return t.stage && t.stage !== '排队中' ? `思考中 · ${t.stage}` : '思考中'
}

function fileNames(t: ConflictTaskItem): string[] {
  return t.name.split('、').filter(Boolean)
}

async function removeTask(t: ConflictTaskItem) {
  try {
    await ElMessageBox.confirm(`删除「${t.name}」的检测记录？`, '删除', { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.deleteConflictTask(t.task_id)
    expanded.delete(t.task_id)
    delete details[t.task_id]
    thoughtOverride.delete(t.task_id)
    await Promise.all([loadSessions(), loadTasks()])
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : String(e))
  }
}

function exportReport(t: ConflictTaskItem) {
  const a = document.createElement('a')
  a.href = api.conflictExportUrl(t.task_id)
  a.download = `conflict-report-${t.task_id}.md`
  a.click()
}

function locOf(c: ConflictItem, side: 'a' | 'b'): string {
  const full = side === 'a' ? c.locate_a : c.locate_b
  if (full) return full
  const file = side === 'a' ? c.file_a : c.file_b
  const subject = side === 'a' ? c.subject_a : c.subject_b
  const chunk = side === 'a' ? c.chunk_a : c.chunk_b
  return [file, subject, chunk != null ? `块${chunk}` : ''].filter(Boolean).join(' · ')
}

function sevType(severity: string): TagType {
  if (severity === '高') return 'danger'
  if (severity === '中') return 'warning'
  return 'info'
}

let hadActive = false
function tick() {
  if (activeTask.value) {
    void loadTasks()
    hadActive = true
  } else if (hadActive) {
    hadActive = false
    void loadSessions()
  }
}

onMounted(async () => {
  try {
    config.value = await api.conflictConfig()
  } catch {
    config.value = null
  }
  await Promise.all([loadSessions(), loadTasks()])
  timer = window.setInterval(tick, 1500)
})

onUnmounted(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<style scoped>
.conflict-shell {
  display: flex;
  gap: 16px;
  height: calc(100vh - 124px);
  min-height: 420px;
}
.session-rail {
  width: 224px;
  flex: 0 0 224px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.rail-head {
  height: 52px;
  flex: 0 0 52px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px 0 14px;
  border-bottom: 1px solid var(--border);
}
.rail-title { font-size: 14px; font-weight: 650; }
.rail-list { flex: 1; min-height: 0; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 4px; }
.rail-empty { padding: 24px 8px; text-align: center; color: var(--muted); font-size: 12px; }
.rail-item { position: relative; padding: 9px 28px 9px 11px; border-radius: 8px; cursor: pointer; }
.rail-item:hover { background: var(--surface-soft); }
.rail-item.is-active { background: var(--primary-soft); }
.rail-item-title { font-size: 13px; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rail-item-meta { font-size: 11px; color: var(--muted); margin-top: 3px; }
.rail-del { position: absolute; top: 7px; right: 6px; font-size: 15px; line-height: 1; color: var(--muted); padding: 0 4px; min-width: auto; }
.rail-del:hover { color: #d35b52; }
.conflict-chat {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
}
.conflict-chat :deep(.page-head) {
  flex: 0 0 auto;
  margin-bottom: 12px;
}
.chat-stream {
  flex: 1;
  overflow-y: auto;
  padding-right: 4px;
}
.chat-welcome {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  opacity: 0.55;
}
.welcome-mark,
.assistant-mark {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: var(--primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 700;
  flex: 0 0 auto;
}
.welcome-mark {
  width: 52px;
  height: 52px;
  font-size: 22px;
  border-radius: 14px;
}
.welcome-text {
  font-size: 14px;
}
.turn {
  margin-bottom: 22px;
}
.msg {
  display: flex;
  gap: 10px;
}
.msg.user {
  justify-content: flex-end;
  margin-bottom: 12px;
}
.user-bubble {
  max-width: 72%;
  background: var(--primary);
  color: #fff;
  border-radius: 12px 4px 12px 12px;
  padding: 10px 14px;
}
.user-file {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  line-height: 1.8;
}
.assistant-body {
  flex: 1;
  min-width: 0;
}
.thought {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  margin-bottom: 10px;
}
.thought-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 9px 12px;
  cursor: pointer;
  font-size: 13px;
  color: var(--sub);
  user-select: none;
}
.thought-icon {
  color: var(--primary);
}
.thought-arrow {
  margin-left: 2px;
  transition: transform 0.2s;
}
.thought-arrow.open {
  transform: rotate(180deg);
}
.msg-del {
  margin-left: auto;
  opacity: 0.45;
}
.msg-del:hover {
  opacity: 1;
}
.thought-steps {
  border-top: 1px solid var(--border);
  padding: 10px 14px 12px;
}
.thought-step {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 12.5px;
  line-height: 2;
  color: var(--sub);
}
.thought-step.current {
  color: var(--primary);
}
.step-dot {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--surface-soft);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  flex: 0 0 auto;
  color: var(--muted);
}
.thought-step.current .step-dot,
.thought-step:last-child .step-dot {
  background: var(--primary);
  color: #fff;
}
.result-line {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 10px;
}
.result-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 4px;
}
.result-pending,
.result-error {
  font-size: 13px;
  color: var(--muted);
}
.result-error {
  color: #cf4d45;
}
.has-conflict {
  color: #cf4d45;
}
.report {
  margin-top: 12px;
}
.report-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
  font-size: 12.5px;
  opacity: 0.8;
  margin-bottom: 10px;
}
.report-empty {
  font-size: 13px;
  color: var(--muted);
}
.report-item {
  padding: 12px 0 14px;
  border-top: 1px solid var(--border);
}
.report-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.report-no {
  font-size: 15px;
  font-weight: 600;
}
.report-topic {
  font-size: 13px;
  opacity: 0.75;
}
.report-exp {
  font-size: 13px;
  line-height: 1.7;
  margin-bottom: 10px;
}
.report-quote {
  background: var(--surface-soft);
  border-left: 3px solid var(--border-strong);
  border-radius: 6px;
  padding: 9px 11px;
  margin-bottom: 8px;
}
.report-text {
  font-size: 13px;
  line-height: 1.7;
}
.conflict-loc {
  font-size: 12px;
  opacity: 0.72;
  margin-bottom: 4px;
  word-break: break-all;
}
.report-footnote {
  margin-top: 10px;
  font-size: 12px;
  line-height: 1.7;
  opacity: 0.6;
}
.composer {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  border-top: 1px solid var(--border);
  padding-top: 12px;
  margin-top: 8px;
}
.composer-field {
  flex: 1;
  min-width: 0;
  min-height: 36px;
  display: flex;
  align-items: center;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 5px 10px;
  background: var(--surface);
}
.composer-placeholder {
  font-size: 13px;
  color: var(--muted);
}
.picked-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
@media (max-width: 820px) {
  .conflict-shell {
    height: calc(100vh - 150px);
  }
  .session-rail {
    display: none;
  }
  .user-bubble {
    max-width: 88%;
  }
}
</style>
