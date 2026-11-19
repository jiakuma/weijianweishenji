import type { Component } from 'vue'
import { ChatDotRound, Connection, Document, Tickets } from '@element-plus/icons-vue'
import type { AuditModule } from '@/types'

export const moduleIcons: Record<string, Component> = {
  conflict: Connection,
  assistant: ChatDotRound,
  workpaper: Document,
  contract: Tickets,
}

// 后端未启动时侧边栏仍可渲染；后端可用时以 /api/modules 为准
export const fallbackModules: AuditModule[] = [
  { id: 'assistant', name: '审计助手', enabled: true, route: '/assistant', group: '审计模块', order: 10 },
  { id: 'conflict', name: '制度互斥', enabled: true, route: '/conflict', group: '审计模块', order: 20 },
  { id: 'contract', name: '合同审计', enabled: true, route: '/contract', group: '审计模块', order: 30 },
  { id: 'workpaper', name: '底稿审核', enabled: true, route: '/workpaper', group: '审计模块', order: 40 },
]
