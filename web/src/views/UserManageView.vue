<template>
  <div>
    <div class="page-head">
      <h2 class="page-title">用户管理</h2>
      <el-button type="primary" style="margin-left: auto" @click="openCreate">新增用户</el-button>
    </div>

    <div class="card card-pad">
      <el-table :data="users" v-loading="loading" size="small">
        <el-table-column prop="username" label="用户名" min-width="130" />
        <el-table-column prop="displayName" label="姓名" min-width="120" />
        <el-table-column label="角色" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.role === 'admin' ? 'warning' : 'info'">{{ roleLabel(row.role) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.enabled ? 'success' : 'danger'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最后登录" min-width="160">
          <template #default="{ row }">{{ row.lastLoginAt || '—' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" size="small" @click="openReset(row)">重置密码</el-button>
            <el-button link :type="row.enabled ? 'danger' : 'primary'" size="small" @click="toggle(row)">
              {{ row.enabled ? '停用' : '启用' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="editVisible" :title="editingId === null ? '新增用户' : '编辑用户'" width="440px">
      <el-form label-width="86px">
        <el-form-item v-if="editingId === null" label="用户名" required>
          <el-input v-model="editForm.username" placeholder="登录账号" />
        </el-form-item>
        <el-form-item label="姓名" required>
          <el-input v-model="editForm.displayName" />
        </el-form-item>
        <el-form-item v-if="editingId === null" label="密码" required>
          <el-input v-model="editForm.password" type="password" show-password placeholder="至少 8 位" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="editForm.role" style="width: 100%">
            <el-option label="管理员" value="admin" />
            <el-option label="普通用户" value="user" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="confirmEdit">确定</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="resetVisible" title="重置密码" width="420px">
      <div style="font-size: 13px; color: var(--sub); margin-bottom: 10px">为 {{ resetTargetName }} 设置新密码（至少 8 位）</div>
      <el-input v-model="resetPassword" type="password" show-password placeholder="新密码" />
      <template #footer>
        <el-button @click="resetVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="confirmReset">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import type { UserId, UserInfoRow, UserRole } from '@/types'

interface UserForm {
  username: string
  displayName: string
  password: string
  role: UserRole
}

const users = ref<UserInfoRow[]>([])
const loading = ref(false)
const saving = ref(false)
const editVisible = ref(false)
const resetVisible = ref(false)
const editingId = ref<UserId | null>(null)
const resetTargetId = ref<UserId | null>(null)
const resetTargetName = ref('')
const resetPassword = ref('')
const editForm = reactive<UserForm>({ username: '', displayName: '', password: '', role: 'user' })

function roleLabel(role: UserRole): string {
  return role === 'admin' ? '管理员' : '普通用户'
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : '操作失败'
}

async function loadUsers(): Promise<void> {
  loading.value = true
  try {
    users.value = await api.listUsers()
  } catch (err) {
    ElMessage.error(message(err))
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  editingId.value = null
  editForm.username = ''
  editForm.displayName = ''
  editForm.password = ''
  editForm.role = 'user'
  editVisible.value = true
}

function openEdit(value: unknown): void {
  const row = value as UserInfoRow
  editingId.value = row.id
  editForm.username = row.username
  editForm.displayName = row.displayName
  editForm.password = ''
  editForm.role = row.role
  editVisible.value = true
}

async function confirmEdit(): Promise<void> {
  if (!editForm.displayName.trim()) {
    ElMessage.warning('请输入姓名')
    return
  }
  if (editingId.value === null && (!editForm.username.trim() || editForm.password.length < 8)) {
    ElMessage.warning('请输入用户名和至少 8 位的密码')
    return
  }
  saving.value = true
  try {
    if (editingId.value === null) {
      await api.createUser({
        username: editForm.username.trim(),
        displayName: editForm.displayName.trim(),
        password: editForm.password,
        role: editForm.role,
      })
    } else {
      await api.updateUser(editingId.value, {
        displayName: editForm.displayName.trim(),
        role: editForm.role,
      })
    }
    ElMessage.success('已保存')
    editVisible.value = false
    await loadUsers()
  } catch (err) {
    ElMessage.error(message(err))
  } finally {
    saving.value = false
  }
}

function openReset(value: unknown): void {
  const row = value as UserInfoRow
  resetTargetId.value = row.id
  resetTargetName.value = row.displayName || row.username
  resetPassword.value = ''
  resetVisible.value = true
}

async function confirmReset(): Promise<void> {
  if (resetTargetId.value === null) return
  if (resetPassword.value.length < 8) {
    ElMessage.warning('新密码至少 8 位')
    return
  }
  saving.value = true
  try {
    await api.resetPassword(resetTargetId.value, resetPassword.value)
    ElMessage.success('密码已重置')
    resetVisible.value = false
  } catch (err) {
    ElMessage.error(message(err))
  } finally {
    saving.value = false
  }
}

async function toggle(value: unknown): Promise<void> {
  const row = value as UserInfoRow
  const action = row.enabled ? '停用' : '启用'
  try {
    await ElMessageBox.confirm(`确定${action}用户「${row.displayName || row.username}」吗？`, action, {
      type: 'warning',
      confirmButtonText: '确定',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await api.toggleUser(row.id)
    ElMessage.success(`已${action}`)
    await loadUsers()
  } catch (err) {
    ElMessage.error(message(err))
  }
}

onMounted(loadUsers)
</script>
