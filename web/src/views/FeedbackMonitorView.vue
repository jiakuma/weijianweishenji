<template>
  <div>
    <div class="page-head">
      <h2 class="page-title">反馈监控</h2>
      <div class="filter-bar">
        <el-date-picker v-model="dateRange" type="daterange" size="small"
          value-format="YYYY-MM-DD" range-separator="至" start-placeholder="开始日期"
          end-placeholder="结束日期" style="width: 240px" @change="load" />
        <el-select v-model="module" size="small" style="width: 120px" @change="load">
          <el-option label="全部模块" value="" />
          <el-option v-for="m in modules" :key="m.value" :label="m.label" :value="m.value" />
        </el-select>
        <el-select v-model="status" size="small" style="width: 120px" @change="load">
          <el-option label="全部状态" value="" />
          <el-option label="待处理" value="pending" />
          <el-option label="已处理" value="handled" />
          <el-option label="已忽略" value="ignored" />
        </el-select>
      </div>
    </div>

    <div class="card card-pad">
      <el-table :data="rows" v-loading="loading" size="small">
        <el-table-column prop="createdAt" label="时间" width="150" />
        <el-table-column prop="username" label="提交人" width="110" />
        <el-table-column prop="module" label="模块" width="100" />
        <el-table-column prop="question" label="问题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="content" label="建议" min-width="220" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">查看</el-button>
            <el-button v-if="row.status === 'pending'" link type="primary" size="small" @click="handle(row.id, 'handled')">处理</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="detailVisible" title="反馈详情" width="640px">
      <div class="detail-row"><span class="detail-label">账号</span><span>{{ detail.username || detail.ownerId || '—' }}</span></div>
      <div class="detail-row"><span class="detail-label">会话 ID</span><span class="detail-code">{{ detail.sessionId || '—' }}</span></div>
      <div class="detail-row"><span class="detail-label">对话 ID</span><span class="detail-code">{{ detail.qaId || '—' }}</span></div>
      <div class="detail-row"><span class="detail-label">模块</span><span>{{ detail.module || '—' }}</span></div>
      <div class="detail-row"><span class="detail-label">时间</span><span>{{ detail.createdAt || '—' }}</span></div>
      <div class="detail-row"><span class="detail-label">评价</span><span>{{ detail.rating === 'good' ? '有帮助' : detail.rating === 'bad' ? '没帮助' : '—' }}</span></div>
      <div class="detail-section">问题</div>
      <div class="detail-text">{{ detail.question || '—' }}</div>
      <div class="detail-section">回答</div>
      <div class="detail-text">{{ detail.answer || '—' }}</div>
      <div class="detail-section">建议</div>
      <div class="detail-text detail-highlight">{{ detail.content || '—' }}</div>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button v-if="detail.status === 'pending'" type="primary" @click="handle(detail.id, 'handled'); detailVisible = false">标记已处理</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import type { QAFeedbackItem } from '@/types'

const rows = ref<QAFeedbackItem[]>([])
const loading = ref(false)
const status = ref('')
const module = ref('')
const dateRange = ref<string[]>([])
const modules = [
  { value: 'assistant', label: '审计助手' },
  { value: 'conflict', label: '制度互斥' },
  { value: 'contract', label: '合同审计' },
  { value: 'workpaper', label: '底稿审核' },
]
const detailVisible = ref(false)
const detail = ref<QAFeedbackItem>({} as QAFeedbackItem)

function statusLabel(s: string) {
  return ({ pending: '待处理', handled: '已处理', ignored: '已忽略' } as Record<string, string>)[s] || s
}
function statusType(s: string): 'warning' | 'success' | 'info' {
  return s === 'pending' ? 'warning' : s === 'handled' ? 'success' : 'info'
}

async function load() {
  loading.value = true
  try {
    rows.value = await api.listFeedbacks({
      status: status.value,
      module: module.value,
      start: dateRange.value?.[0] || '',
      end: dateRange.value?.[1] || '',
    })
  } catch {
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

function openDetail(row: unknown) {
  detail.value = row as QAFeedbackItem
  detailVisible.value = true
}

async function handle(id: string, next: string) {
  try {
    await api.handleFeedback(id, next)
    ElMessage.success('已更新')
    await load()
  } catch {
    ElMessage.error('操作失败')
  }
}

onMounted(load)
</script>

<style scoped>
.filter-bar { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.detail-row { display: flex; gap: 12px; font-size: 13px; line-height: 2.1; }
.detail-label { color: var(--muted); font-size: 12px; min-width: 60px; flex-shrink: 0; }
.detail-code { font-family: monospace; font-size: 12px; color: var(--sub); word-break: break-all; }
.detail-section { color: var(--muted); font-size: 12px; margin-top: 14px; margin-bottom: 5px; }
.detail-text { font-size: 13px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; padding: 8px 10px; background: var(--surface-soft); border-radius: 6px; max-height: 160px; overflow-y: auto; }
.detail-highlight { background: #f5f7fa; }
</style>
