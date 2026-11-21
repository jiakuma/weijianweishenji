<template>
  <div v-loading="loading">
    <div class="page-head" v-if="doc">
      <div style="display: flex; align-items: center; gap: 10px; min-width: 0">
        <el-button text title="返回上一页" @click="router.back()"><el-icon><ArrowLeft /></el-icon>返回</el-button>
        <h1 class="page-title" style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap">{{ doc.title }}</h1>
        <el-tag size="small" effect="plain" round>{{ doc.format }}</el-tag>
      </div>
      <el-button type="primary" @click="download">
        <el-icon style="margin-right: 5px"><Download /></el-icon>下载原始文件
      </el-button>
    </div>

    <div v-if="doc" class="doc-grid">
      <div class="card" style="padding: 4px 20px 18px">
        <el-tabs v-model="tab">
          <el-tab-pane name="original">
            <template #label>原始文件</template>
            <div style="padding: 8px 0 14px">
              <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px">
                <span class="muted" style="font-size: 12px">共 {{ fulltext.length }} 字符</span>
                <el-button text size="small" style="margin-left: auto" @click="copyFulltext" :disabled="!fulltext.length">复制全文</el-button>
              </div>
              <div class="original-preview">
                <iframe v-if="previewKind === 'pdf'" :src="originalUrl" title="原始文件预览" />
                <img v-else-if="previewKind === 'image'" :src="originalUrl" alt="原始图片" />
                <iframe v-else-if="previewKind === 'office'" :src="previewUrl" title="原始文件预览" />
                <div v-else-if="previewKind === 'text'" class="fulltext-view" style="max-height: 72vh; background: #f7f8fa; padding: 16px; border-radius: 8px; border: 1px solid var(--border)">
                  <template v-for="(b, i) in rawBlocks" :key="i">
                    <div v-if="b.type === 'text'">{{ b.text }}</div>
                    <figure v-else class="ft-image">
                      <img :src="b.url" :alt="b.alt || '图片'" referrerpolicy="no-referrer" loading="lazy" />
                    </figure>
                  </template>
                  <span v-if="!rawBlocks.length" class="muted">暂无正文内容，请点击右上角「下载原始文件」查看</span>
                </div>
                <div v-else class="muted" style="font-size: 13px; text-align: center; padding: 30px">
                  该格式暂不支持内联预览<br />请点击右上角「下载原始文件」查看
                </div>
              </div>
              <div v-if="assets.length" style="margin-top: 16px">
                <div class="section-title">关联图片资源（{{ assets.length }}）</div>
                <div style="display: flex; gap: 10px; flex-wrap: wrap">
                  <a v-for="a in assets" :key="a.id" :href="fileUrl(a.url)" target="_blank" rel="noopener" :title="a.name">
                    <img :src="fileUrl(a.url)" :alt="a.name" style="width: 108px; height: 82px; object-fit: cover; border: 1px solid var(--border); border-radius: 8px" />
                  </a>
                </div>
              </div>
            </div>
          </el-tab-pane>

          <el-tab-pane name="chunks">
            <template #label>知识切片 ({{ doc.chunks.length }})</template>
            <div style="padding: 8px 0 4px">
              <div v-for="chunk in doc.chunks" :key="chunk.id" class="chunk">
                <div class="chunk-head">
                  <el-tag size="small" effect="plain">第 {{ chunk.page }} 页</el-tag>
                  <span>{{ chunk.section }}</span>
                </div>
                <div class="chunk-text">{{ chunk.text }}</div>
              </div>
              <div v-if="!doc.chunks.length" class="empty-box">该资料未生成切片</div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>

      <div class="card card-pad">
        <div class="section-title">资料信息</div>
        <el-form label-width="76px" label-position="left" @submit.prevent>
          <el-form-item label="分类">
            <span class="cat-readonly">{{ doc?.category || '未分类' }}</span>
          </el-form-item>
          <el-form-item label="标签">
            <div class="tag-editor">
              <el-tag
                v-for="t in tags"
                :key="t"
                closable
                type="primary"
                effect="light"
                :disable-transitions="true"
                @close="removeTag(t)"
              >{{ t }}</el-tag>
              <el-input
                v-model="tagInput"
                size="small"
                class="tag-input"
                placeholder="输入标签后回车添加"
                @keyup.enter="addTag"
                @blur="addTag"
              />
            </div>
          </el-form-item>
        </el-form>
        <div class="storage-line"><span class="sub">来源</span><span>{{ doc.source }}</span></div>
        <div class="storage-line"><span class="sub">页数</span><span>{{ doc.pages }}</span></div>
        <div class="storage-line"><span class="sub">大小</span><span>{{ doc.sizeKb }} KB</span></div>
        <div class="storage-line"><span class="sub">导入时间</span><span>{{ doc.importedAt }}</span></div>
        <div class="storage-line"><span class="sub">完整原文</span><span>{{ fulltext.length }} 字符</span></div>
        <div class="storage-line"><span class="sub">知识切片</span><span>{{ doc.chunks.length }} 段</span></div>
        <div class="storage-line"><span class="sub">关联资源</span><span>{{ assets.length }} 个</span></div>
        <el-button style="width: 100%; margin-top: 14px" @click="exportDoc">导出资料</el-button>
      </div>
    </div>

    <div v-else-if="!loading" class="empty-box">资料不存在或已被删除</div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Download } from '@element-plus/icons-vue'
import { api, fileUrl } from '@/api'
import { useKnowledgeStore } from '@/stores/knowledge'
import type { AssetItem, KnowledgeDoc } from '@/types'

const route = useRoute()
const router = useRouter()
const store = useKnowledgeStore()
const doc = ref<KnowledgeDoc | null>(null)
const fulltext = ref('')
const assets = ref<AssetItem[]>([])
const tags = ref<string[]>([])
const tagInput = ref('')
const tab = ref('original')
const rawText = ref('')

type FulltextBlock = { type: 'text' | 'image'; text: string; url: string; alt: string }

function toBlocks(text: string): FulltextBlock[] {
  const out: FulltextBlock[] = []
  let buffer: string[] = []
  const flush = () => {
    if (buffer.length) {
      out.push({ type: 'text', text: buffer.join('\n'), url: '', alt: '' })
      buffer = []
    }
  }
  for (const line of (text || '').split('\n')) {
    const m = line.trim().match(/^!\[([^\]]*)\]\(([^)]+)\)$/)
    if (m) {
      flush()
      out.push({ type: 'image', text: '', url: m[2], alt: m[1] })
    } else {
      buffer.push(line)
    }
  }
  flush()
  return out
}

const isOffice = computed(() => !!doc.value && ['DOC', 'DOCX', 'PPT', 'PPTX'].includes(doc.value.format))

const previewKind = computed(() => {
  if (!doc.value) return ''
  const f = doc.value.format
  if (f === 'PDF') return 'pdf'
  if (f === '图片') return 'image'
  // DOC/DOCX/PPT/PPTX：装了 LibreOffice 就内联渲染，否则回退为已解析全文
  if (isOffice.value) return doc.value.officePreview ? 'office' : 'text'
  if (f === 'MD' || f === 'TXT' || f === '网页') return 'text'
  return ''
})
// 文本预览内容来源：office 类用已解析全文，MD/TXT 类取原始文件
const previewText = computed(() => (isOffice.value ? fulltext.value : rawText.value))
const rawBlocks = computed(() => toBlocks(previewText.value))
const previewUrl = computed(() => fileUrl(`/api/docs/${doc.value?.id ?? ''}/preview`))
const loading = ref(true)

const originalUrl = computed(() => fileUrl(`/api/docs/${doc.value?.id ?? ''}/file`))

async function load() {
  loading.value = true
  const id = String(route.params.id)
  try {
    const [d, f, a] = await Promise.all([api.getDoc(id), api.getFulltext(id), api.getAssets(id)])
    doc.value = d?.id ? d : null
    fulltext.value = f.text || ''
    assets.value = a
    tags.value = [...(d.tags || [])]
    if (previewKind.value === 'text' && !isOffice.value) {
      try {
        rawText.value = await (await fetch(originalUrl.value)).text()
      } catch {
        rawText.value = ''
      }
    } else {
      rawText.value = ''
    }
    if (route.query.chunk) {
      tab.value = 'chunks'
    } else if (previewKind.value === 'pdf' || previewKind.value === 'image') {
      tab.value = 'original'
    } else {
      tab.value = 'original'
    }
  } finally {
    loading.value = false
  }
}

function download() {
  const a = document.createElement('a')
  a.href = originalUrl.value
  a.download = doc.value?.title ?? 'file'
  a.click()
}

async function copyFulltext() {
  await navigator.clipboard.writeText(fulltext.value)
  ElMessage.success('已复制全文')
}

async function saveTags() {
  if (!doc.value) return
  await api.setTags(doc.value.id, tags.value)
  doc.value.tags = [...tags.value]
  await store.loadDocs()
  ElMessage.success('标签已更新')
}

function addTag() {
  const v = tagInput.value.trim()
  tagInput.value = ''
  if (!v || tags.value.includes(v)) return
  tags.value = [...tags.value, v]
  saveTags()
}

function removeTag(t: string) {
  tags.value = tags.value.filter((x) => x !== t)
  saveTags()
}

async function exportDoc() {
  if (!doc.value) return
  const { filename, content } = await api.exportDoc(doc.value.id)
  const url = URL.createObjectURL(new Blob([content], { type: 'text/markdown;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(() => void load())
watch(() => route.params.id, () => void load())
</script>

<style scoped>
.tag-editor {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.tag-editor .el-tag {
  margin: 0;
}
.tag-editor .tag-input {
  flex: 1;
  min-width: 140px;
  max-width: 220px;
}
.tag-editor .tag-input :deep(.el-input__wrapper) {
  background: var(--el-fill-color-blank);
}
.cat-readonly {
  display: inline-block;
  padding: 4px 10px;
  font-size: 13px;
  line-height: 1.4;
  color: var(--el-text-color-regular);
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}
</style>
