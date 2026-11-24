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
        { path: 'import', name: 'import', component: () => import('@/views/ImportView.vue'), meta: { title: '文件导入', requiresAdmin: true } },
        { path: 'users', name: 'users', component: () => import('@/views/UserManageView.vue'), meta: { title: '用户管理', requiresAdmin: true } },
        { path: 'feedback', name: 'feedback', component: () => import('@/views/FeedbackMonitorView.vue'), meta: { title: '反馈监控', requiresAdmin: true } },
      ],
    },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.beforeEach(async (to) => {
  const token = getAccessToken()
  const auth = useAuthStore()
  if (!token && to.path !== '/login') return { path: '/login', replace: true }
  if (token && to.path === '/login') return { path: '/assistant', replace: true }
  // 首屏或直接粘贴 URL 进入受保护页面时，currentUser 尚未异步加载。
  // 先拉一次身份再判断权限，否则管理员守卫会因 currentUser 为空而被跳过。
  if (token && !auth.currentUser) await auth.fetchMe()
  if (auth.mustChangePassword && to.path !== '/change-password') return { path: '/change-password', replace: true }
  // 身份未加载/加载失败一律不放行管理员页面，后端 403 只是最后兜底。
  if (to.meta.requiresAdmin && !auth.isAdmin) return { path: '/assistant', replace: true }
  return true
})

export default router
