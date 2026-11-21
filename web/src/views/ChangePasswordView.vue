<template>
  <div class="auth-page">
    <div class="auth-card card card-pad">
      <div class="auth-title">修改密码</div>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="当前密码">
          <el-input v-model="oldPassword" type="password" show-password autocomplete="current-password" />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="newPassword" type="password" show-password autocomplete="new-password" placeholder="至少 8 位" />
        </el-form-item>
        <el-form-item label="确认新密码">
          <el-input v-model="confirmPassword" type="password" show-password autocomplete="new-password" @keyup.enter="submit" />
        </el-form-item>
        <div v-if="error" class="auth-error">{{ error }}</div>
        <div class="auth-actions">
          <el-button v-if="!auth.mustChangePassword" @click="router.replace('/assistant')">返回</el-button>
          <el-button type="primary" native-type="submit" :loading="loading">保存</el-button>
        </div>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const oldPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const loading = ref(false)
const error = ref('')

async function submit(): Promise<void> {
  if (loading.value) return
  if (!oldPassword.value) {
    error.value = '请输入当前密码'
    return
  }
  if (newPassword.value.length < 8) {
    error.value = '新密码至少 8 位'
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    error.value = '两次输入的新密码不一致'
    return
  }
  loading.value = true
  error.value = ''
  try {
    await api.changePassword(oldPassword.value, newPassword.value)
    auth.markPasswordChanged()
    ElMessage.success('密码已修改')
    await router.replace('/assistant')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '修改失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-page { min-height: 100vh; display: grid; place-items: center; background: var(--bg); padding: 20px; }
.auth-card { width: 380px; }
.auth-title { font-size: 18px; font-weight: 650; margin-bottom: 14px; }
.auth-error { color: #d35b52; font-size: 12.5px; margin-bottom: 10px; line-height: 1.5; }
.auth-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
