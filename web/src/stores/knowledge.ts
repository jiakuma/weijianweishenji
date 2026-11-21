import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/api'
import type {
  CategoryItem,
  CategoryNode,
  ImportTask,
  KnowledgeDoc,
  QASession,
  Settings,
  StorageInfo,
} from '@/types'

const emptySettings: Settings = {
  displayName: '我的知识库',
  pageSize: 10,
  viewMode: 'list',
  autoSummary: true,
  autoClassify: true,
  autoChunk: true,
  retryCount: 2,
  supportedFormats: ['PDF', 'PPTX', 'DOCX', 'MD', 'TXT', '图片'],
  llmBaseUrl: '',
  llmApiKey: '',
  accessToken: '',
  zhihuCookie: '',
  llmModel: '',
  llmThinking: false,
  temperature: 0.2,
  maxTokens: 2048,
  embeddingBaseUrl: '',
  embeddingModel: '',
  originalsDir: '',
  parsedDir: '',
  databasePath: '',
}

export const useKnowledgeStore = defineStore('knowledge', () => {
  const docs = ref<KnowledgeDoc[]>([])
  const tree = ref<CategoryNode[]>([])
  const categories = ref<CategoryItem[]>([])
  const total = ref(0)
  const tasks = ref<ImportTask[]>([])
  const settings = ref<Settings>({ ...emptySettings })
  const storage = ref<StorageInfo | null>(null)

  const keyword = ref('')
  const currentCategory = ref('')
  const directOnly = ref(false)
  const starredOnly = ref(false)
  const sort = ref<'time' | 'name'>('time')
  const viewMode = ref<'list' | 'card'>('list')
  const mapOpen = ref(false)
  const loading = ref(false)
  const backendReady = ref(false)

  // 智能问答会话（侧边栏常驻列表 + 问答页共用）
  const qaSessions = ref<QASession[]>([])
  const qaSessionId = ref('')
  // 侧边栏请求切换会话时，按模块通知对应问答页（多入口各自注册，互不覆盖）
  const qaSwitchHandlers = ref<Record<string, (id: string) => void>>({})

  async function loadQaSessions(module = '') {
    qaSessions.value = await api.listSessions(module)
  }
  async function createQaSession(module = 'assistant'): Promise<string | null> {
    try {
      const s = await api.createSession(module)
      await loadQaSessions(module)
      qaSessionId.value = s.id
      return s.id
    } catch {
      return null
    }
  }
  async function removeQaSession(id: string, module = '') {
    await api.deleteSession(id)
    // 必须按当前模块重载，否则会把其他模块的会话（含空的新会话）混进侧边栏。
    await loadQaSessions(module)
    if (qaSessionId.value === id) {
      const next = qaSessions.value[0]
      qaSessionId.value = next ? next.id : ''
    }
  }
  function setQaSwitchHandler(module: string, fn: ((id: string) => void) | null) {
    if (fn) qaSwitchHandlers.value[module] = fn
    else delete qaSwitchHandlers.value[module]
  }
  /** 退出/切换账号时丢弃会话状态，下一次进入页面由 loadQaSessions 按新身份重新拉取。 */
  function clearQaState(): void {
    qaSessions.value = []
    qaSessionId.value = ''
    qaSwitchHandlers.value = {}
  }
  function requestQaSessionSwitch(id: string, module?: string) {
    qaSessionId.value = id
    const key = module || 'assistant'
    qaSwitchHandlers.value[key]?.(id)
  }

  async function loadDocs() {
    loading.value = true
    try {
      const list = await api.listDocs({
        keyword: keyword.value,
        category: currentCategory.value,
        starredOnly: starredOnly.value,
        sort: sort.value,
      })
      docs.value = directOnly.value
        ? list.filter((d) => d.category === currentCategory.value)
        : list
    } finally {
      loading.value = false
    }
  }

  async function loadCategories() {
    const data = await api.categories()
    tree.value = data.tree
    categories.value = data.items
    total.value = data.total
  }

  async function loadTasks() {
    tasks.value = await api.listTasks()
  }

  async function loadSettings() {
    settings.value = await api.getSettings()
    viewMode.value = settings.value.viewMode === 'card' ? 'card' : 'list'
  }

  async function loadStorage() {
    storage.value = await api.getStorage()
  }

  async function loadAll() {
    backendReady.value = await api.health()
    if (!backendReady.value) return
    await Promise.all([loadDocs(), loadCategories(), loadTasks(), loadSettings(), loadStorage()])
  }

  function selectCategory(name: string) {
    currentCategory.value = name
    directOnly.value = false
    void loadDocs()
  }

  /** 只看直接挂在该分类名下、未归入任何子分类的资料 */
  function selectDirect(name: string) {
    currentCategory.value = name
    directOnly.value = true
    void loadDocs()
  }

  function setKeyword(value: string) {
    keyword.value = value
    void loadDocs()
  }

  function setSort(value: 'time' | 'name') {
    sort.value = value
    void loadDocs()
  }

  async function setViewMode(value: 'list' | 'card') {
    viewMode.value = value
    settings.value = await api.saveSettings({ viewMode: value })
  }

  async function refreshAfterImport() {
    await Promise.all([loadDocs(), loadCategories(), loadTasks(), loadStorage()])
  }

  let poller = 0
  function startPolling() {
    if (poller) return
    poller = window.setInterval(() => {
      const active = tasks.value.some((t) => t.status === '解析中' || t.status === '等待中')
      if (!active) return
      void (async () => {
        await loadTasks()
        await Promise.all([loadDocs(), loadCategories(), loadStorage()])
      })()
    }, 900)
  }

  function stopPolling() {
    if (poller) window.clearInterval(poller)
    poller = 0
  }

  return {
    docs, tree, categories, total, tasks, settings, storage,
    keyword, currentCategory, directOnly, starredOnly, sort, viewMode, mapOpen, loading, backendReady,
    qaSessions, qaSessionId,
    loadQaSessions, createQaSession, removeQaSession,
    setQaSwitchHandler, requestQaSessionSwitch, clearQaState,
    loadDocs, loadCategories, loadTasks, loadSettings, loadStorage, loadAll,
    selectCategory, selectDirect, setKeyword, setSort, setViewMode, refreshAfterImport,
    startPolling, stopPolling,
  }
})
