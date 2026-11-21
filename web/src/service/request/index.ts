// 统一 HTTP 请求层：组件不直接发请求，全部经此封装。
import axios, { type AxiosRequestConfig, type InternalAxiosRequestConfig } from 'axios'

const BASE = String(import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')
// local 模式用 x-access-token header，不需要 cookie；跨域携带 cookie 会触发
// 浏览器要求 Access-Control-Allow-Credentials，后端未开启时会拦截所有响应。
// 仅 SSO 跨域部署时设为 true，并确保后端 allow_credentials 同步开启。
const WITH_CREDENTIALS = String(import.meta.env.VITE_WITH_CREDENTIALS ?? '').toLowerCase() === 'true'
const TOKEN_KEY = 'kb-access-token'

export type ApiResult<T> = { data: T | null; error: string | null }

export function getAccessToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) || ''
}

export function setAccessToken(token: string): void {
  if (token) sessionStorage.setItem(TOKEN_KEY, token)
  else sessionStorage.removeItem(TOKEN_KEY)
}

export function fileUrl(path: string): string {
  return `${BASE}${path}`
}

export function failMessage(status: number, path: string): string {
  if (status === 401) return '登录已失效，请重新登录或检查访问令牌'
  if (status === 403) return '没有执行此操作的权限'
  return `${path} → ${status}`
}

const http = axios.create({
  baseURL: BASE,
  timeout: 120000,
  withCredentials: WITH_CREDENTIALS,
})

function attachToken(config: InternalAxiosRequestConfig): InternalAxiosRequestConfig {
  const token = getAccessToken()
  if (token) config.headers.set('X-Access-Token', token)
  return config
}

http.interceptors.request.use(attachToken)

// 后端新契约 { data: ... }；旧接口直接返回值。两者共存期间自动解包。
function unwrap<T>(payload: unknown): T {
  if (
    payload && typeof payload === 'object' && !Array.isArray(payload) &&
    Object.keys(payload).length === 1 && 'data' in payload
  ) {
    return (payload as { data: T }).data
  }
  return payload as T
}

function toConfig(path: string, init?: RequestInit): AxiosRequestConfig {
  const headers: Record<string, string> = {}
  new Headers(init?.headers).forEach((value, key) => { headers[key] = value })
  return {
    url: path,
    method: (init?.method || 'GET') as AxiosRequestConfig['method'],
    data: init?.body,
    headers,
    signal: init?.signal ?? undefined,
  }
}

/** 标准请求入口：失败抛 Error，成功返回解包后的业务数据。 */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    const res = await http.request<unknown>(toConfig(path, init))
    return unwrap<T>(res.data)
  } catch (err) {
    if (axios.isAxiosError(err)) {
      throw new Error(failMessage(err.response?.status ?? 0, path), { cause: err })
    }
    throw err
  }
}

/** 标准返回结构：{ data, error }，供需要就地处理错误的场景使用。 */
export async function safeRequest<T>(path: string, init?: RequestInit): Promise<ApiResult<T>> {
  try {
    return { data: await request<T>(path, init), error: null }
  } catch (err) {
    return { data: null, error: err instanceof Error ? err.message : String(err) }
  }
}

/** FormData 上传：axios 自动设置 multipart 边界。 */
export async function uploadRequest<T>(path: string, form: FormData): Promise<T> {
  try {
    const res = await http.post<unknown>(path, form)
    return unwrap<T>(res.data)
  } catch (err) {
    const message = axios.isAxiosError(err)
      ? (err.response?.data as { error?: string } | undefined)?.error ||
        failMessage(err.response?.status ?? 0, path)
      : String(err)
    throw new Error(message, { cause: err })
  }
}

/** SSE 流式请求：浏览器流只能走 fetch，此处集中封装，组件内不裸调。 */
export async function streamRequest(
  path: string,
  body: unknown,
  onChunk: (text: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = getAccessToken()
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-Access-Token': token } : {}),
    },
    credentials: WITH_CREDENTIALS ? 'include' : 'same-origin',
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) throw new Error(failMessage(res.status, path))
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() || ''
    for (const part of parts) onChunk(part)
  }
  if (buffer.trim()) onChunk(buffer)
}

export const post = (body: unknown): RequestInit => ({
  method: 'POST',
  body: JSON.stringify(body),
})

export default http
