// 兼容入口：原 @/api 引用保持不变，实现已按域拆到 @/service/api。
export { api } from '@/service/api'
export { fileUrl, getAccessToken, setAccessToken } from '@/service/request'
