<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">审</span>
        <span>{{ t('app.name') }}</span>
      </div>
      <nav class="nav">
        <template v-for="(group, gi) in navGroups" :key="gi">
          <div class="nav-group">{{ group.label }}</div>
          <template v-for="item in group.items" :key="item.key">
            <div
              v-if="item.enabled"
              class="nav-item"
              :class="{ 'is-active': isActive(item.path) }"
              @click="go(item.path)"
            >
              <el-icon><component :is="item.icon" /></el-icon>
              <span>{{ item.label }}</span>
            </div>
            <div v-else class="nav-item is-disabled">
              <el-icon><component :is="item.icon" /></el-icon>
              <span>{{ item.label }}</span>
              <el-tag class="nav-tag" size="small" type="info">{{ t('common.disabled') }}</el-tag>
            </div>
          </template>
        </template>
      </nav>

      <div class="nav-foot">
        <div>{{ store.storage ? `${t('common.localData')} ${store.storage.totalMb.toFixed(1)} MB` : t('common.localData') }}</div>
        <div class="storage-bar">
          <span :style="{ width: `${Math.min(100, ((store.storage?.totalMb ?? 0) / 200) * 100)}%` }" />
        </div>
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <el-button class="menu-trigger" text @click="mobileNav = true">
          <el-icon :size="18"><Menu /></el-icon>
        </el-button>
        <!-- 顶栏显示当前所在页，避免与左侧品牌重复 -->
        <span class="topbar-title">{{ currentPageTitle }}</span>
        <div class="topbar-right">
          <el-dropdown trigger="click" @command="onUserCommand">
            <button class="account-btn">
              <span class="account-avatar">{{ (auth.currentUser?.displayName || auth.currentUser?.username || '用').slice(0, 1) }}</span>
              <span class="account-name">{{ auth.currentUser?.displayName || auth.currentUser?.username || '' }}</span>
              <el-icon class="account-caret"><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <template v-if="auth.canSwitchUser">
                  <div class="switch-user-head">切换账号</div>
                  <el-dropdown-item
                    v-for="u in switchableUsers"
                    :key="u.id"
                    :command="`switch:${u.id}`"
                    :disabled="u.id === auth.currentUser?.id"
                  >
                    <span class="switch-user-row">
                      <span>{{ u.displayName }}</span>
                      <el-tag size="small" :type="u.role === 'admin' ? 'warning' : 'info'">
                        {{ u.role === 'admin' ? '管理员' : '普通用户' }}
                      </el-tag>
                      <span v-if="u.id === auth.currentUser?.id" class="switch-user-current">当前</span>
                    </span>
                  </el-dropdown-item>
                </template>
                <el-dropdown-item command="password" :divided="auth.canSwitchUser">修改密码</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="main-scroll" :class="{ 'is-chat': isQa }">
        <div class="page-body">
          <!-- 会话栏在顶栏之下，不再与顶栏并排，所有页面共用同一条顶栏 -->
          <aside v-if="isQa" class="qa-rail">
            <div class="qa-rail-head">
              <span class="qa-rail-title">{{ t('common.sessionHistory') }}</span>
              <el-button text size="small" :icon="Plus" @click="newSessionFromSidebar">{{ t('common.newSession') }}</el-button>
            </div>
            <div class="qa-rail-list">
              <div v-if="!store.qaSessions.length" class="qa-rail-empty">{{ t('assistant.noSession') }}</div>
              <div
                v-for="s in store.qaSessions"
                :key="s.id"
                class="qa-rail-item"
                :class="{ 'is-active': s.id === store.qaSessionId }"
                @click="pickSession(s.id)"
              >
                <div class="qa-rail-item-title">{{ s.title || t('common.newSession') }}</div>
                <div class="qa-rail-item-meta">{{ s.updatedAt }} · {{ s.count }} 条</div>
                <el-button text size="small" class="qa-rail-del" @click.stop="removeSessionFromSidebar(s.id)">×</el-button>
              </div>
            </div>
          </aside>
          <div class="page">
            <router-view v-slot="{ Component }">
              <keep-alive>
                <component :is="Component" :key="route.path" />
              </keep-alive>
            </router-view>
          </div>
        </div>
      </main>
    </div>

    <el-drawer v-model="mobileNav" :title="t('nav.title')" size="210px" direction="ltr">
      <div class="nav">
        <template v-for="(group, gi) in navGroups" :key="gi">
          <div class="nav-group">{{ group.label }}</div>
          <div
            v-for="item in group.items"
            :key="item.key"
            class="nav-item"
            :class="{ 'is-active': item.enabled && isActive(item.path), 'is-disabled': !item.enabled }"
            @click="onMobileNav(item)"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </div>
        </template>
      </div>

      <div class="side-sessions mobile">
        <div class="side-sessions-head">
          <span>{{ t('common.sessionHistory') }}</span>
          <el-button text size="small" :icon="Plus" @click="newSessionFromSidebarMobile">{{ t('common.newSession') }}</el-button>
        </div>
        <div class="side-sessions-list">
          <div v-if="!store.qaSessions.length" class="side-sessions-empty">{{ t('assistant.noSession') }}</div>
          <div
            v-for="s in store.qaSessions"
            :key="s.id"
            class="side-session"
            :class="{ 'is-active': s.id === store.qaSessionId }"
            @click="pickSessionMobile(s.id)"
          >
            <div class="side-session-title">{{ s.title || t('common.newSession') }}</div>
            <div class="side-session-meta">{{ s.updatedAt }} · {{ s.count }} 条</div>
            <el-button text size="small" class="side-session-del" @click.stop="removeSessionFromSidebar(s.id)">×</el-button>
          </div>
        </div>
      </div>
    </el-drawer>

    <KnowledgeMap v-model="store.mapOpen" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ChatDotRound, ArrowDown, Collection, Document, FolderOpened, Menu, Plus, Setting, UserFilled } from '@element-plus/icons-vue'
import KnowledgeMap from '@/components/KnowledgeMap.vue'
import { useKnowledgeStore } from '@/stores/knowledge'
import { useAuthStore } from '@/stores/auth'
import { api } from '@/api'
import { fallbackModules, moduleIcons } from '@/modules'
import type { AuditModule, UserInfoRow } from '@/types'

interface NavItem {
  key: string
  label: string
  path: string
  icon: Component
  enabled: boolean
}

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const store = useKnowledgeStore()
const auth = useAuthStore()
const mobileNav = ref(false)
const modules = ref<AuditModule[]>(fallbackModules)
const switchableUsers = ref<UserInfoRow[]>([])

const CHAT_PATHS = ['/assistant', '/conflict', '/contract', '/workpaper']
const isQa = computed(() => CHAT_PATHS.some((p) => route.path.startsWith(p)))
const currentModule = computed(() => {
  const p = CHAT_PATHS.find((x) => route.path.startsWith(x))
  return p ? p.slice(1) : ''
})

const assistantModules = computed<NavItem[]>(() => {
  const order = ['assistant', 'conflict', 'contract', 'workpaper']
  const items: NavItem[] = []
  for (const id of order) {
    const m = modules.value.find((x) => x.id === id)
    const path = m?.route || `/${id}`
    items.push({
      key: id,
      label: m?.name || ({ assistant: t('nav.assistant'), conflict: t('nav.conflict'), contract: t('nav.contract'), workpaper: t('nav.workpaper') } as Record<string, string>)[id],
      path,
      icon: moduleIcons[id] ?? (id === 'assistant' ? ChatDotRound : Document),
      enabled: true,
    })
  }
  // 未启用的模块（如底稿审核）照常展示为灰项
  for (const m of modules.value) {
    if (order.includes(m.id)) continue
    items.push({ key: m.id, label: m.name, path: m.route, icon: moduleIcons[m.id] ?? Document, enabled: m.enabled })
  }
  return items
})

const navGroups = computed(() => {
  const basic: NavItem[] = [
    { key: 'library', label: t('nav.library'), path: '/library', icon: FolderOpened, enabled: true },
    { key: 'issues', label: t('nav.issues'), path: '/issues', icon: Collection, enabled: true },
    { key: 'settings', label: t('nav.settings'), path: '/settings', icon: Setting, enabled: true },
  ]
  if (auth.isAdmin) {
    basic.push({ key: 'users', label: '用户管理', path: '/users', icon: UserFilled, enabled: true })
  }
  return [
    { label: t('nav.groupAudit'), items: assistantModules.value },
    { label: t('nav.groupBasic'), items: basic },
  ]
})

/** 顶栏显示当前所在页名称（不再重复平台名） */
const currentPageTitle = computed(() => {
  const all = navGroups.value.flatMap((g) => g.items)
  const hit = all.find((item) => isActive(item.path))
  return hit?.label || t('app.name')
})

async function onUserCommand(command: string): Promise<void> {
  if (command.startsWith('switch:')) {
    await switchAccount(command.slice('switch:'.length))
    return
  }
  if (command === 'password') {
    await router.push('/change-password')
    return
  }
  if (command === 'logout') {
    await auth.logout()
    await router.replace('/login')
  }
}

/** 切到另一个账号后，旧的会话高亮键必须清掉，否则重新加载时会误恢复上一人的会话。 */
function clearLastSessionKeys(): void {
  for (const m of ['assistant', 'conflict', 'contract', 'workpaper']) {
    try { localStorage.removeItem(`audit-last-session-${m}`) } catch { /* 隐私模式下忽略 */ }
  }
}

async function switchAccount(userId: string): Promise<void> {
  try {
    await auth.switchUser(userId)
    clearLastSessionKeys()
    ElMessage.success('已切换账号')
    // 整体重载并固定落到助手页：新身份的权限与会话列表都由服务端重新拉取。
    window.location.href = '/assistant'
  } catch (error) {
    ElMessage.error(`切换账号失败：${error instanceof Error ? error.message : String(error)}`)
  }
}

async function pickSession(id: string) {
  store.requestQaSessionSwitch(id, currentModule.value)
}
async function newSessionFromSidebar() {
  const id = await store.createQaSession(currentModule.value || 'assistant')
  if (id) store.requestQaSessionSwitch(id, currentModule.value)
}
async function removeSessionFromSidebar(id: string) {
  await store.removeQaSession(id, currentModule.value)
  if (store.qaSessionId) store.requestQaSessionSwitch(store.qaSessionId, currentModule.value)
}
async function pickSessionMobile(id: string) {
  store.requestQaSessionSwitch(id, currentModule.value)
  mobileNav.value = false
}
async function newSessionFromSidebarMobile() {
  const id = await store.createQaSession(currentModule.value || 'assistant')
  if (id) store.requestQaSessionSwitch(id, currentModule.value)
  mobileNav.value = false
}

function isActive(path: string) {
  if (!path) return false
  if (path === '/library') return route.path === '/library' || route.path.startsWith('/document')
  if (path === '/issues') return route.path === '/issues'
  return route.path === path || route.path.startsWith(path + '/')
}

function go(path: string) {
  void router.push(path)
}

function onMobileNav(item: NavItem) {
  if (item.enabled) go(item.path)
  mobileNav.value = false
}

onMounted(async () => {
  await auth.fetchMe()
  if (auth.canSwitchUser) {
    try {
      switchableUsers.value = await api.switchableUsers()
    } catch {
      switchableUsers.value = []
    }
  }
  await store.loadAll()
  if (!store.backendReady) {
    ElMessage.error('后端未连接，请在 server 目录运行 python run.py（端口 5281）')
    return
  }
  store.startPolling()
  try {
    modules.value = await api.listAuditModules()
  } catch {
    modules.value = fallbackModules
  }
})

// 切换助手模块时，会话栏只显示该模块的会话
watch(currentModule, (m) => {
  if (m) void store.loadQaSessions(m)
})

onUnmounted(() => store.stopPolling())
</script>

<style scoped>
.topbar-right { margin-left: auto; display: flex; align-items: center; }
.account-btn {
  display: flex; align-items: center; gap: 9px; height: 38px; padding: 0 10px 0 5px;
  background: transparent; border: 1px solid transparent; border-radius: 20px; cursor: pointer;
  color: var(--text); transition: background .15s, border-color .15s;
}
.account-btn:hover { background: var(--surface-soft); border-color: var(--border); }
.account-avatar {
  width: 28px; height: 28px; border-radius: 50%; flex: 0 0 28px;
  background: linear-gradient(135deg, var(--primary), var(--accent)); color: #fff;
  display: grid; place-items: center; font-size: 13px; font-weight: 650;
}
.account-name { font-size: 13px; font-weight: 500; white-space: nowrap; }
.account-caret { font-size: 12px; color: var(--muted); }
.switch-user-head { padding: 6px 12px 6px; font-size: 11.5px; font-weight: 600; color: var(--muted); letter-spacing: .4px; }
.switch-user-row { display: flex; align-items: center; gap: 8px; }
.switch-user-current { margin-left: auto; font-size: 12px; color: var(--muted); }
</style>
