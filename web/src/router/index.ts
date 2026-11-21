import { createRouter, createWebHistory } from 'vue-router'
import MainLayout from '@/layouts/MainLayout.vue'
import { getAccessToken } from '@/api'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { title: '登录' } },
    { path: '/change-password', name: 'change-password', component: () => import('@/views/ChangePasswordView.vue'), meta: { title: '修改密码' } },
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
        { path: 'users', name: 'users', component: () => import('@/views/UserManageView.vue'), meta: { title: '用户管理', requiresAdmin: true } },
      ],
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach((to) => {
  const token = getAccessToken()
  const auth = useAuthStore()
  if (!token && to.path !== '/login') return { path: '/login', replace: true }
  if (token && to.path === '/login') return { path: '/assistant', replace: true }
  // store 尚未加载时不拦截，由后端 403 兜底跳修改密码。
  if (auth.mustChangePassword && to.path !== '/change-password') return { path: '/change-password', replace: true }
  if (to.meta.requiresAdmin && auth.currentUser && !auth.isAdmin) return { path: '/assistant', replace: true }
  return true
})

export default router
