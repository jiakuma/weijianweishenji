// i18n 初始化：zh-cn / en-us 必须成对维护。
import { createI18n } from 'vue-i18n'
import type { App } from 'vue'
import zhCn from './langs/zh-cn'
import enUs from './langs/en-us'

const STORAGE_KEY = 'audit-platform-locale'

export function detectLocale(): 'zh-cn' | 'en-us' {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'zh-cn' || saved === 'en-us') return saved
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh-cn' : 'en-us'
}

const i18n = createI18n({
  legacy: false,
  locale: detectLocale(),
  fallbackLocale: 'zh-cn',
  messages: { 'zh-cn': zhCn, 'en-us': enUs },
})

export function setLocale(locale: 'zh-cn' | 'en-us'): void {
  localStorage.setItem(STORAGE_KEY, locale)
  i18n.global.locale.value = locale
}

export function setupI18n(app: App): void {
  app.use(i18n)
}

export default i18n
