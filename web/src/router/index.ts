import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: MainLayout,
      redirect: '/assistant',
      children: [
        { path: 'assistant', name: 'assistant', component: () => import('@/views/AssistantView.vue'), meta: { title: '审计助手', module: 'assistant' } },
        { path: 'conflict', name: 'conflict', component: () => import('@/views/AssistantView.vue'), meta: { title: '制度互斥', module: 'conflict' } },
        { path: 'contract', name: 'contract', component: () => import('@/views/AssistantView.vue'), meta: { title: '合同审计', module: 'contract' } },
        { path: 'workpaper', name: 'workpaper', component: () => import('@/views/AssistantView.vue'), meta: { title: '底稿审核', module: 'workpaper' } },
        { path: 'qa', redirect: '/assistant' },
        { path: 'library', name: 'library', component: () => import('@/views/LibraryView.vue'), meta: { title: '资料库' } },
        { path: 'issues', name: 'issues', component: () => import('@/views/IssueLibraryView.vue'), meta: { title: '历史问题库' } },
        { path: 'document/:id', name: 'document', component: () => import('@/views/DocumentView.vue'), meta: { title: '资料详情' } },
        { path: 'import', name: 'import', component: () => import('@/views/ImportView.vue'), meta: { title: '文件导入' } },
        { path: 'settings', name: 'settings', component: () => import('@/views/SettingsView.vue'), meta: { title: '系统设置' } },
      ],
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

export default router
