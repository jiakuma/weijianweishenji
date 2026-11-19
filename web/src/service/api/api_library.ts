// 资料库、导入任务、分类相关接口。
import { fileUrl, getAccessToken, post, request, uploadRequest } from '@/service/request'
import type {
  AssetItem, CategoryItem, DocQuery, ImportTask, KnowledgeDoc, UploadItem,
} from '@/types'

export const libraryApi = {
  async health(): Promise<boolean> {
    try {
      await request('/api/health', { signal: AbortSignal.timeout(3000) })
      return true
    } catch {
      return false
    }
  },

  listDocs(query: DocQuery = {}) {
    return request<KnowledgeDoc[]>('/api/docs/query', post(query))
  },

  getDoc(id: string) {
    return request<KnowledgeDoc>(`/api/docs/${id}`)
  },

  getFulltext(id: string) {
    return request<{ docId: string; text: string; charCount: number }>(`/api/docs/${id}/fulltext`)
  },

  getAssets(id: string) {
    return request<AssetItem[]>(`/api/docs/${id}/assets`)
  },

  toggleStar(id: string) {
    return request<{ starred: boolean }>(`/api/docs/${id}/star`, { method: 'POST' })
  },

  setCategory(id: string, category: string) {
    return request<{ ok: boolean }>(`/api/docs/${id}/category`, post({ category }))
  },

  setTags(id: string, tags: string[]) {
    return request<{ ok: boolean }>(`/api/docs/${id}/tags`, post({ tags }))
  },

  async deleteDoc(id: string, purge: boolean) {
    await request(`/api/docs/${id}?purge=${purge}`, { method: 'DELETE' })
  },

  reparseDoc(id: string) {
    return request<{ ok: boolean; taskId?: string }>(`/api/docs/${id}/reparse`, { method: 'POST' })
  },

  exportDoc(id: string) {
    return request<{ filename: string; content: string }>(`/api/docs/${id}/export`)
  },

  categories() {
    return request<{ items: CategoryItem[]; tree: import('@/types').CategoryNode[]; total: number }>('/api/categories')
  },

  addCategory(name: string, parentId: string) {
    return request<{ ok: boolean; item?: CategoryItem; message?: string }>('/api/categories', post({ name, parentId }))
  },

  renameCategory(id: string, name: string) {
    return request<{ ok: boolean; message?: string }>(`/api/categories/${id}`, {
      method: 'PUT', body: JSON.stringify({ name }),
    })
  },

  async deleteCategory(id: string) {
    await request(`/api/categories/${id}`, { method: 'DELETE' })
  },

  listTasks() {
    return request<ImportTask[]>('/api/imports')
  },

  /** 资料库导入：分类由用户指定或按文件夹层级生成。 */
  uploadFiles(items: UploadItem[], source = '本地文件', batchId = ''): Promise<ImportTask[]> {
    const form = new FormData()
    for (const item of items) form.append('files', item.file, item.file.name)
    form.append('relative_paths', JSON.stringify(items.map((i) => i.relativePath)))
    form.append('categories', JSON.stringify(items.map((i) => i.category || '')))
    form.append('source', source)
    form.append('batch_id', batchId)
    return uploadRequest<ImportTask[]>('/api/imports/files', form)
  },

  /** 智能助手附件：只进后端临时区，不写资料库。 */
  uploadAssistantFiles(
    items: UploadItem[], sessionId = '',
  ): Promise<Array<{ id: string; name: string; filename: string; sizeKb: number }>> {
    const form = new FormData()
    for (const item of items) form.append('files', item.file, item.file.name)
    form.append('session_id', sessionId)
    return uploadRequest('/api/assistant/uploads', form)
  },

  uploadLink(url: string, name: string) {
    return request<ImportTask>('/api/imports/links', post({ url, name, source: '网页链接' }))
  },

  retryTask(id: string) {
    return request<{ ok: boolean }>(`/api/imports/${id}/retry`, { method: 'POST' })
  },

  reparseTask(id: string) {
    return request<{ ok: boolean }>(`/api/imports/${id}/reparse`, { method: 'POST' })
  },

  clearTasks(scope: 'finished' | 'all') {
    return request<{ ok: boolean; removed: number }>(`/api/imports?scope=${scope}`, { method: 'DELETE' })
  },

  previewUrl(id: string) {
    return fileUrl(`/api/docs/${id}/preview`)
  },

  token: getAccessToken,
}
