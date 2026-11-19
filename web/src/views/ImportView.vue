<template>
  <div>
    <div class="page-head">
      <h2 class="page-title">导入资料</h2>
    </div>
    <div class="card card-pad" style="margin-bottom: 14px">
      <div
        class="dropzone"
        :class="{ 'is-over': isOver }"
        @click="fileInput?.click()"
        @dragover.prevent="isOver = true"
        @dragleave.prevent="isOver = false"
        @drop.prevent="onDrop"
      >
        <el-icon><UploadFilled /></el-icon>
        <div style="margin-top: 10px; font-size: 14px">拖放文件到这里，或点击选择文件</div>
        <div class="muted" style="font-size: 12px; margin-top: 6px">PDF · PPTX · DOCX · MD · TXT · 常见图片</div>
        <input ref="fileInput" type="file" multiple :accept="accept" style="display: none" @change="onPick" />
        <input ref="dirInput" type="file" multiple webkitdirectory directory style="display: none" @change="onPickDir" />
      </div>

      <div style="display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; align-items: center">
        <el-button @click="fileInput?.click()"><el-icon style="margin-right: 5px"><Document /></el-icon>选择文件</el-button>
        <el-button @click="selectFolder"><el-icon style="margin-right: 5px"><Folder /></el-icon>选择文件夹</el-button>
        <el-input v-model="link" placeholder="粘贴网页链接" style="width: 300px" clearable @keyup.enter="importLink" />
        <el-button @click="importLink">导入链接</el-button>
        <el-button type="primary" style="margin-left: auto" :disabled="!queue.length" :loading="uploading" @click="startImport">
          开始导入{{ queue.length ? ` (${queue.length})` : '' }}
        </el-button>
      </div>
    </div>

    <div v-if="queue.length" class="card card-pad" style="margin-bottom: 14px">
      <div class="section-title">
        待导入文件
        <el-button text size="small" style="margin-left: auto" @click="queue = []">清空</el-button>
      </div>
      <div v-for="(item, i) in queue" :key="`${item.relativePath}-${i}`" class="queue-item">
        <div class="file-badge" :class="`tone-${tone(item.file.name)}`" style="width: 28px; height: 32px; flex-basis: 28px; font-size: 8px">
          {{ ext(item.file.name) }}
        </div>
        <div class="queue-name">
          <div>{{ item.file.name }}</div>
          <div class="muted" style="font-size: 11.5px">{{ item.relativePath }}</div>
        </div>
        <span class="muted nowrap" style="font-size: 12px">{{ size(item.file.size) }}</span>
        <el-select
          v-if="!isFolderItem(item)"
          v-model="item.category"
          size="small"
          clearable
          filterable
          allow-create
          default-first-option
          placeholder="未分类"
          style="width: 170px"
        >
          <el-option v-for="c in catOptions" :key="c.value" :label="c.label" :value="c.value" />
        </el-select>
        <span v-else class="muted nowrap" style="font-size: 12px">按文件夹分类</span>
        <el-button text size="small" @click="queue.splice(i, 1)"><el-icon><Close /></el-icon></el-button>
      </div>
    </div>

    <div class="card card-pad">
      <div class="section-title">
        导入任务
        <el-dropdown trigger="click" style="margin-left: auto" @command="clearTasks">
          <el-button text size="small">清除记录<el-icon style="margin-left: 3px"><ArrowDown /></el-icon></el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="finished">清除已结束的任务</el-dropdown-item>
              <el-dropdown-item command="all" divided>清除全部任务记录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>

      <div v-if="!store.tasks.length" class="empty-box">还没有导入任务</div>
      <div v-for="task in store.tasks" :key="task.id" class="task-item">
        <div class="task-main" style="min-width: 0">
          <div class="task-name">{{ task.name }}</div>
          <div
            v-if="task.status === '失败' && task.message"
            class="task-sub"
            style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis"
            :title="task.message"
          >{{ task.message }}</div>
          <el-progress
            v-if="task.status === '解析中' || task.status === '等待中'"
            :percentage="task.progress"
            :stroke-width="4"
            style="margin-top: 7px"
          />
        </div>
        <el-tag :type="tagType(task.status)" size="small" effect="light">{{ task.status }}</el-tag>
        <el-button v-if="task.status === '已完成' && task.docId" class="task-action" text size="small" @click="openDoc(task.docId)">查看结果</el-button>
        <el-button v-if="task.status === '失败'" class="task-action" text size="small" @click="retry(task.id)">重试</el-button>
        <el-button v-if="task.status === '已完成' && task.docId" class="task-action" text size="small" @click="reparse(task.id)">重新解析</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, Close, Document, Folder, UploadFilled } from '@element-plus/icons-vue'
import { api } from '@/api'
import { useKnowledgeStore } from '@/stores/knowledge'
import type { CategoryNode, TaskStatus, UploadItem } from '@/types'

const store = useKnowledgeStore()
const router = useRouter()
const fileInput = ref<HTMLInputElement | null>(null)
const dirInput = ref<HTMLInputElement | null>(null)
const isOver = ref(false)
const queue = ref<UploadItem[]>([])
const link = ref('')
const uploading = ref(false)

const accept = '.pdf,.ppt,.pptx,.doc,.docx,.md,.markdown,.txt,.html,.htm,.png,.jpg,.jpeg,.gif,.webp,.bmp,.svg,.tif,.tiff'

function isFolderItem(item: UploadItem) {
  return item.relativePath.includes('/')
}

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

function ext(name: string) {
  const e = name.split('.').pop()?.toUpperCase() ?? 'FILE'
  return e.slice(0, 4)
}

function tone(name: string) {
  const e = name.split('.').pop()?.toLowerCase() ?? ''
  return ({ pdf: 'pdf', docx: 'docx', doc: 'docx', pptx: 'pptx', ppt: 'pptx', md: 'md', markdown: 'md', txt: 'txt', html: 'web', htm: 'web' } as Record<string, string>)[e]
    ?? (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'tif', 'tiff'].includes(e) ? 'image' : 'txt')
}

function size(bytes: number) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function addFiles(files: FileList | null, fromFolder: boolean) {
  if (!files?.length) return
  const items: UploadItem[] = Array.from(files).map((file) => ({
    file,
    relativePath: fromFolder
      ? ((file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name)
      : file.name,
  }))
  queue.value = [...queue.value, ...items]
}

function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  addFiles(input.files, false)
  input.value = ''
}

function onPickDir(event: Event) {
  const input = event.target as HTMLInputElement
  addFiles(input.files, true)
  input.value = ''
}

function onDrop(event: DragEvent) {
  isOver.value = false
  const dt = event.dataTransfer
  // 支持直接拖入整个文件夹（Chromium 的 directory entry）
  const items = dt?.items
  if (items && items.length && typeof (items[0] as any).webkitGetAsEntry === 'function') {
    const entries: any[] = []
    for (const it of Array.from(items)) {
      const entry = (it as any).webkitGetAsEntry?.()
      if (entry) entries.push(entry)
    }
    if (entries.some((e) => e.isDirectory)) {
      void Promise.all(entries.map((e) => walkEntry(e, '', []))).then((lists) => {
        const merged = lists.flat()
        if (merged.length) queue.value = [...queue.value, ...merged]
      })
      return
    }
  }
  addFiles(dt?.files ?? null, false)
}

// 用 File System Access API 选文件夹：点一下选整个目录，自动递归
async function selectFolder() {
  const picker = (window as any).showDirectoryPicker
  if (typeof picker !== 'function') {
    // 不支持的浏览器回退到 webkitdirectory
    dirInput.value?.click()
    return
  }
  try {
    const handle = await picker.call(window)
    const items: UploadItem[] = []
    // 把选中的根文件夹名作为相对路径首段，确保资料库里创建的是「这个文件夹」
    await walkHandle(handle, handle.name, items)
    if (items.length) queue.value = [...queue.value, ...items]
  } catch (err: any) {
    if (err?.name === 'AbortError') return // 用户取消
    // 出错回退
    dirInput.value?.click()
  }
}

async function walkHandle(dirHandle: any, prefix: string, out: UploadItem[]) {
  for await (const entry of dirHandle.values()) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name
    if (entry.kind === 'file') {
      const file = await entry.getFile()
      out.push({ file, relativePath: rel })
    } else if (entry.kind === 'directory') {
      await walkHandle(entry, rel, out)
    }
  }
}

// 拖拽目录项的递归读取
async function walkEntry(entry: any, prefix: string, out: UploadItem[]): Promise<UploadItem[]> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => entry.file(resolve, reject))
    out.push({ file, relativePath: prefix ? `${prefix}/${entry.name}` : entry.name })
  } else if (entry.isDirectory) {
    const reader = entry.createReader()
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const batch: any[] = await new Promise((resolve, reject) => reader.readEntries(resolve, reject))
      if (!batch.length) break
      for (const child of batch) await walkEntry(child, prefix ? `${prefix}/${entry.name}` : entry.name, out)
    }
  }
  return out
}

async function startImport() {
  if (!queue.value.length || uploading.value) return
  uploading.value = true
  try {
    const batchId = `IMP_${Date.now()}`
    const created = await api.uploadFiles(queue.value, '本地文件', batchId)
    queue.value = []
    await store.refreshAfterImport()
    const skipped = created.filter((t) => t.status === '重复跳过').length
    if (skipped) ElMessage.warning(`${skipped} 个文件内容重复，已跳过`)
    if (created.length - skipped) ElMessage.success(`已加入 ${created.length - skipped} 个处理任务`)
  } catch (error) {
    ElMessage.error(`导入失败：${String(error)}`)
  } finally {
    uploading.value = false
  }
}

async function importLink() {
  const url = link.value.trim()
  if (!url) return
  try {
    const task = await api.uploadLink(url, '')
    link.value = ''
    await store.refreshAfterImport()
    if (task.status === '失败') ElMessage.error(task.message || '链接导入失败')
    else ElMessage.success('已加入处理任务')
  } catch {
    ElMessage.error('链接导入失败')
  }
}

function tagType(status: TaskStatus) {
  if (status === '已完成') return 'success'
  if (status === '失败') return 'danger'
  if (status === '解析中' || status === '等待中') return 'warning'
  return 'info'
}

function openDoc(id: string) {
  void router.push(`/document/${id}`)
}

async function retry(id: string) {
  await api.retryTask(id)
  await store.loadTasks()
  ElMessage.success('已重新加入队列')
}

async function reparse(id: string) {
  await api.reparseTask(id)
  await store.refreshAfterImport()
  ElMessage.success('已加入重新解析队列')
}

async function clearTasks(scope: string) {
  if (scope === 'all') {
    try {
      await ElMessageBox.confirm('清除全部任务记录？这不会删除已入库的资料和原始文件。', '清除记录', {
        type: 'warning', confirmButtonText: '清除', cancelButtonText: '取消',
      })
    } catch {
      return
    }
  }
  const res = await api.clearTasks(scope as 'finished' | 'all')
  await store.loadTasks()
  ElMessage.success(`已清除 ${res.removed} 条记录`)
}
</script>
