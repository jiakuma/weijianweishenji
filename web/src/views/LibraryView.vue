<template>
  <div>
    <div class="library-layout">
      <CategoryTree />
      <div class="library-pane">
        <div class="pane-toolbar">
          <el-button @click="store.mapOpen = true">
            <el-icon style="margin-right: 5px"><Share /></el-icon>知识地图
          </el-button>
          <el-button v-if="auth.isAdmin" type="primary" @click="goImport">
            <el-icon style="margin-right: 5px"><Upload /></el-icon>导入资料
          </el-button>
        </div>

        <div v-loading="store.loading">
          <template v-if="store.viewMode === 'list'">
            <div v-for="doc in paged" :key="doc.id" class="doc-row" @click="open(doc)">
              <div class="file-badge" :class="`tone-${FORMAT_TONE[doc.format] ?? 'txt'}`">{{ doc.format }}</div>
              <div class="doc-body">
                <div class="doc-title">{{ doc.title }}</div>
                <div class="tag-list" style="margin-top: 6px">
                  <el-tag v-for="t in doc.tags.slice(0, 3)" :key="t" size="small" effect="plain" round>{{ t }}</el-tag>
                </div>
              </div>
              <div class="doc-meta">
                <div>{{ doc.importedAt }}</div>
                <div>{{ formatSize(doc.sizeKb) }}</div>
              </div>
              <div class="doc-actions" @click.stop>
                <el-button text size="small" @click="api.toggleStar(doc.id).then(store.loadDocs)">
                  <el-icon :size="15" :color="doc.starred ? '#e2a03a' : undefined">
                    <StarFilled v-if="doc.starred" /><Star v-else />
                  </el-icon>
                </el-button>
                <el-dropdown trigger="click" @command="(cmd: string) => onCommand(cmd, doc)">
                  <el-button text size="small"><el-icon><MoreFilled /></el-icon></el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="category">修改分类</el-dropdown-item>
                      <el-dropdown-item command="tags">修改标签</el-dropdown-item>
                      <el-dropdown-item command="download" divided>下载原始文件</el-dropdown-item>
                      <el-dropdown-item command="export">导出资料</el-dropdown-item>
                      <el-dropdown-item command="reparse">重新解析</el-dropdown-item>
                      <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>
            </div>
          </template>

          <div v-else class="card-grid">
            <div v-for="doc in paged" :key="doc.id" class="doc-card" @click="open(doc)">
              <div style="display: flex; align-items: flex-start; gap: 10px">
                <div class="file-badge" :class="`tone-${FORMAT_TONE[doc.format] ?? 'txt'}`">{{ doc.format }}</div>
                <div style="flex: 1; min-width: 0">
                  <div class="doc-title">{{ doc.title }}</div>
                </div>
              </div>
              <div class="tag-list" style="margin-top: 10px">
                <el-tag v-for="t in doc.tags.slice(0, 2)" :key="t" size="small" effect="plain" round>{{ t }}</el-tag>
              </div>
              <div class="doc-card-foot">
                <span>{{ doc.importedAt }}</span>
                <el-button text size="small" @click.stop="api.toggleStar(doc.id).then(store.loadDocs)">
                  <el-icon :size="15" :color="doc.starred ? '#e2a03a' : undefined">
                    <StarFilled v-if="doc.starred" /><Star v-else />
                  </el-icon>
                </el-button>
              </div>
            </div>
          </div>

          <div v-if="!store.docs.length && !store.loading" class="empty-box">这个分类下还没有资料</div>
        </div>

        <div class="pane-footer">
          <el-pagination
            v-model:current-page="page"
            :page-size="store.settings.pageSize"
            :total="store.docs.length"
            layout="total, prev, pager, next"
            background
          />
        </div>
      </div>
    </div>

    <el-dialog v-model="editDialog" title="修改标签" width="420px">
      <el-select
        v-model="editTags"
        multiple
        filterable
        allow-create
        default-first-option
        placeholder="输入后回车添加标签"
        style="width: 100%"
      >
        <el-option v-for="t in allTags" :key="t" :label="t" :value="t" />
      </el-select>
      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="primary" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="categoryDialog" title="修改分类" width="420px">
      <el-select
        v-model="editCategory"
        filterable
        allow-create
        default-first-option
        placeholder="选择或输入分类（用 / 表示层级）"
        style="width: 100%"
      >
        <el-option v-for="c in catOptions" :key="c.value" :label="c.label" :value="c.value" />
      </el-select>
      <template #footer>
        <el-button @click="categoryDialog = false">取消</el-button>
        <el-button type="primary" @click="saveCategory">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="deleteDialog" title="删除资料" width="440px">
      <div style="font-size: 13.5px; line-height: 1.8">
        确定删除「{{ target?.title }}」？
      </div>
      <el-radio-group v-model="purge" style="margin-top: 14px; display: flex; flex-direction: column; align-items: flex-start; gap: 8px">
        <el-radio value="keep">仅删除知识库副本，保留原始文件</el-radio>
        <el-radio value="purge">同时删除原始文件</el-radio>
      </el-radio-group>
      <el-alert v-if="purge === 'purge'" type="warning" :closable="false" style="margin-top: 12px" title="原始文件将被永久删除，操作不可恢复" />
      <template #footer>
        <el-button @click="deleteDialog = false">取消</el-button>
        <el-button type="danger" @click="confirmDelete">确定删除</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { MoreFilled, Share, Star, StarFilled, Upload } from '@element-plus/icons-vue'
import { api, fileUrl } from '@/api'
import CategoryTree from '@/components/CategoryTree.vue'
import { useKnowledgeStore } from '@/stores/knowledge'
import { useAuthStore } from '@/stores/auth'
import { FORMAT_TONE } from '@/types'
import type { CategoryNode, KnowledgeDoc } from '@/types'

const store = useKnowledgeStore()
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const page = ref(1)
const editDialog = ref(false)
const editTags = ref<string[]>([])
const categoryDialog = ref(false)
const editCategory = ref('')
const deleteDialog = ref(false)
const purge = ref<'keep' | 'purge'>('keep')
const target = ref<KnowledgeDoc | null>(null)

const paged = computed(() =>
  store.docs.slice((page.value - 1) * store.settings.pageSize, page.value * store.settings.pageSize),
)
const allTags = computed(() => [...new Set(store.docs.flatMap((d) => d.tags))])
const catOptions = computed<Array<{ label: string; value: string }>>(() => {
  const out: Array<{ label: string; value: string }> = []
  const walk = (nodes: CategoryNode[], prefix: string) => {
    for (const n of nodes) {
      if (n.name === '未分类') continue
      const path = prefix ? `${prefix}/${n.name}` : n.name
      out.push({ label: path, value: path })
      if (n.children?.length) walk(n.children, path)
    }
  }
  walk(store.tree, '')
  return out
})

onMounted(() => {
  if (route.query.map === '1') store.mapOpen = true
})

function formatSize(kb: number) {
  return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`
}

function open(doc: KnowledgeDoc) {
  void router.push(`/document/${doc.id}`)
}

function goImport() {
  void router.push('/import')
}

function onCommand(cmd: string, doc: KnowledgeDoc) {
  target.value = doc
  if (cmd === 'category') {
    editCategory.value = doc.category && doc.category !== '未分类' ? doc.category : ''
    categoryDialog.value = true
  } else if (cmd === 'tags') {
    editTags.value = [...doc.tags]
    editDialog.value = true
  } else if (cmd === 'download') {
    const a = document.createElement('a')
    a.href = fileUrl(`/api/docs/${doc.id}/file`)
    a.download = doc.title
    a.click()
  } else if (cmd === 'export') {
    void (async () => {
      const { filename, content } = await api.exportDoc(doc.id)
      const url = URL.createObjectURL(new Blob([content], { type: 'text/markdown;charset=utf-8' }))
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
    })()
  } else if (cmd === 'reparse') {
    void api.reparseDoc(doc.id).then(() => {
      ElMessage.success('已加入重新解析队列')
      void store.refreshAfterImport()
    })
  } else if (cmd === 'delete') {
    purge.value = 'keep'
    deleteDialog.value = true
  }
}

async function saveCategory() {
  const doc = target.value
  if (!doc) return
  await api.setCategory(doc.id, editCategory.value.trim() || '未分类')
  categoryDialog.value = false
  await Promise.all([store.loadDocs(), store.loadCategories()])
  ElMessage.success('已保存')
}

async function saveEdit() {
  const doc = target.value
  if (!doc) return
  await api.setTags(doc.id, editTags.value)
  editDialog.value = false
  await Promise.all([store.loadDocs(), store.loadCategories()])
  ElMessage.success('已保存')
}

async function confirmDelete() {
  const doc = target.value
  if (!doc) return
  if (purge.value === 'purge') {
    try {
      await ElMessageBox.confirm(`再次确认：永久删除「${doc.title}」的原始文件？`, '删除原始文件', {
        type: 'warning', confirmButtonText: '永久删除', cancelButtonText: '取消',
      })
    } catch {
      return
    }
  }
  await api.deleteDoc(doc.id, purge.value === 'purge')
  deleteDialog.value = false
  await Promise.all([store.loadDocs(), store.loadCategories(), store.loadStorage()])
  ElMessage.success(purge.value === 'purge' ? '资料与原始文件已删除' : '已删除知识库副本，原始文件保留')
}
</script>
