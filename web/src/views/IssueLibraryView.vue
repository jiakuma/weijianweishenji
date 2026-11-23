<template>
  <div>
    <div class="issue-toolbar">
      <el-select v-model="filters.year" class="issue-filter-year" placeholder="全部年度">
        <el-option label="全部年度" value="" />
        <el-option v-for="y in yearOptions" :key="y" :label="y" :value="y" />
      </el-select>
      <el-input v-model="filters.category" class="issue-filter-cat" placeholder="四级分类关键词" clearable @keyup.enter="doSearch" />
      <el-input v-model="filters.subject" class="issue-filter-subject" placeholder="审计主体" clearable @keyup.enter="doSearch" />
      <el-input v-model="filters.q" class="issue-filter-q" placeholder="关键词：表现 / 问题 / 定性依据 / 整改" clearable @keyup.enter="doSearch" />
      <el-button type="primary" @click="doSearch">查询</el-button>
      <el-button @click="resetFilters">重置</el-button>
    </div>

    <div v-loading="loading">
      <el-table :data="items" class="issue-table" row-key="id" stripe @row-click="openIssue">
        <el-table-column label="年度" width="76">
          <template #default="{ row }"><span class="issue-year">{{ row.sourceYear || '—' }}</span></template>
        </el-table-column>
        <el-table-column label="四级分类链" min-width="210">
          <template #default="{ row }">
            <span class="issue-cat" :title="categoryChain(row as AuditIssue)">{{ categoryChain(row as AuditIssue) || '未分类' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="表现形态" min-width="190">
          <template #default="{ row }">{{ clip(row.manifestation, 48) || '—' }}</template>
        </el-table-column>
        <el-table-column label="问题描述" min-width="250">
          <template #default="{ row }">{{ clip(row.problemDesc, 70) || '—' }}</template>
        </el-table-column>
        <el-table-column label="定性依据" min-width="220">
          <template #default="{ row }">{{ clip(row.legalBasis, 60) || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="82" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openIssue(row as AuditIssue)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!items.length && !loading" class="empty-box">没有符合条件的历史问题</div>
    </div>

    <div class="issue-pager">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="size"
        :page-sizes="[10, 20, 50, 100]"
        :total="total"
        layout="total, sizes, prev, pager, next"
        background
        @current-change="load"
        @size-change="onSizeChange"
      />
    </div>

    <el-drawer v-model="drawerVisible" title="历史问题详情" size="620px">
      <div v-if="active" class="issue-detail">
        <div class="issue-detail-title">{{ active.catL4 || active.category || '历史审计问题' }}</div>
        <div class="issue-detail-meta">
          {{ active.sourceYear }} 年度 · {{ active.auditSubject || '未标注主体' }} · {{ active.auditProject || '未标注项目' }}
        </div>

        <div class="issue-detail-section">
          <div class="issue-detail-label">分类链</div>
          <div class="issue-detail-cats">
            <el-tag v-for="(cat, ci) in catTags(active)" :key="`${ci}-${cat}`" size="small" effect="plain" round>
              {{ ci + 1 }}级 · {{ cat }}
            </el-tag>
            <span v-if="!catTags(active).length">未分类</span>
          </div>
        </div>

        <div class="issue-detail-section">
          <div class="issue-detail-label">问题表现</div>
          <div class="issue-detail-text">{{ active.manifestation || '—' }}</div>
        </div>
        <div class="issue-detail-section">
          <div class="issue-detail-label">问题描述</div>
          <div class="issue-detail-text">{{ active.problemDesc || '—' }}</div>
        </div>
        <div class="issue-detail-section">
          <div class="issue-detail-label">定性依据</div>
          <div class="issue-detail-text">{{ active.legalBasis || '—' }}</div>
        </div>
        <div class="issue-detail-section">
          <div class="issue-detail-label">整改措施</div>
          <div class="issue-detail-text">{{ active.remediation || '—' }}</div>
        </div>

        <div class="issue-detail-section issue-detail-grid">
          <div><span class="issue-detail-label">整改状态</span><span>{{ active.remediationStatus || '—' }}</span></div>
          <div><span class="issue-detail-label">整改类型</span><span>{{ active.remediationType || '—' }}</span></div>
          <div><span class="issue-detail-label">整改时限</span><span>{{ active.remediationDeadline || '—' }}</span></div>
          <div><span class="issue-detail-label">披露形式</span><span>{{ active.disclosure || '—' }}</span></div>
          <div><span class="issue-detail-label">审计类型</span><span>{{ active.auditType || '—' }}</span></div>
          <div><span class="issue-detail-label">来源文件</span><span>{{ active.sourceFile || '—' }}</span></div>
        </div>

        <div class="issue-detail-notice">历史问题仅供检索参考，定性须由审计人员复核。</div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/api'
import type { AuditIssue, AuditIssueStats } from '@/types'

const route = useRoute()

const items = ref<AuditIssue[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const loading = ref(false)
const drawerVisible = ref(false)
const active = ref<AuditIssue | null>(null)
const stats = ref<AuditIssueStats>({ dbTotal: 0, vectorTotal: 0, byYear: {}, bySubject: {} })

const filters = ref({ year: '', category: '', subject: '', q: '' })

const yearOptions = computed<string[]>(() => {
  const years = Object.keys(stats.value.byYear || {}).filter((y) => y && y !== '0').sort().reverse()
  return years.length ? years : ['2025', '2024']
})
function clip(text: string | undefined, length: number): string {
  const value = (text || '').replace(/\s+/g, ' ').trim()
  return value.length > length ? `${value.slice(0, length)}…` : value
}

function categoryChain(row: AuditIssue): string {
  return row.category || [row.catL1, row.catL2, row.catL3, row.catL4].filter(Boolean).join(' → ')
}

function catTags(row: AuditIssue): string[] {
  return [row.catL1, row.catL2, row.catL3, row.catL4].filter((x): x is string => Boolean(x))
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const data = await api.listAuditIssues({
      page: page.value,
      size: size.value,
      year: filters.value.year,
      category: filters.value.category,
      subject: filters.value.subject,
      q: filters.value.q,
    })
    items.value = data.items || []
    total.value = data.total || 0
  } catch {
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function doSearch(): void {
  page.value = 1
  void load()
}

function resetFilters(): void {
  filters.value = { year: '', category: '', subject: '', q: '' }
  doSearch()
}

function onSizeChange(): void {
  page.value = 1
  void load()
}

function openIssue(row: AuditIssue): void {
  active.value = row
  drawerVisible.value = true
}

async function openById(id: string): Promise<void> {
  if (!id) return
  try {
    const row = await api.getAuditIssue(id)
    if (row && row.id) {
      active.value = row
      drawerVisible.value = true
    }
  } catch {
    /* 详情加载失败不阻断列表浏览 */
  }
}

onMounted(async () => {
  await load()
  try {
    stats.value = await api.auditIssueStats()
  } catch {
    /* 统计失败时仍保留列表的总数 */
  }
  await openById(String(route.query.id || ''))
})

watch(() => route.query.id, (value) => {
  void openById(String(value || ''))
})
</script>

<style scoped>
.issue-toolbar { display: flex; flex-wrap: wrap; gap: 9px; align-items: center; margin-bottom: 10px; }
.issue-filter-year { width: 132px; }
.issue-filter-cat { width: 176px; }
.issue-filter-subject { width: 158px; }
.issue-filter-q { width: 268px; }
.issue-table { width: 100%; font-size: 12.5px; }
.issue-table :deep(.el-table__row) { cursor: pointer; }
.issue-year { font-weight: 600; color: var(--primary-dark); }
.issue-cat { display: block; max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--sub); }
.issue-pager { display: flex; justify-content: flex-end; padding: 12px 0; }

.issue-detail-title { font-size: 17px; font-weight: 650; color: var(--text); line-height: 1.5; }
.issue-detail-meta { margin-top: 6px; font-size: 12.5px; color: var(--muted); }
.issue-detail-section { margin-top: 18px; }
.issue-detail-label { display: block; margin-bottom: 5px; font-size: 12px; font-weight: 600; color: var(--sub); }
.issue-detail-cats { display: flex; flex-wrap: wrap; gap: 6px; }
.issue-detail-text { font-size: 13px; line-height: 1.8; white-space: pre-wrap; word-break: break-word; }
.issue-detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 18px; }
.issue-detail-grid .issue-detail-label { display: inline; margin-right: 7px; font-weight: 500; }
.issue-detail-grid span { font-size: 12.5px; word-break: break-word; }
.issue-detail-notice { margin-top: 22px; padding: 9px 11px; font-size: 12px; color: #9a6a12; background: #fff8e8; border-radius: 6px; }
</style>
