<template>
  <div class="auth-page">
    <div class="auth-card card card-pad">
      <div class="auth-brand"><span class="auth-mark">审</span>审计工作平台</div>
      <div class="auth-title">账号登录</div>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名">
          <el-input v-model="username" autocomplete="username" placeholder="请输入用户名" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="password"
            type="password"
            show-password
            autocomplete="current-password"
            placeholder="请输入密码"
            @keyup.enter="submit"
          />
        </el-form-item>
        <div v-if="error" class="auth-error">{{ error }}</div>
        <el-button type="primary" class="auth-submit" native-type="submit" :loading="loading">登录</el-button>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const username = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function submit(): Promise<void> {
  if (loading.value) return
  if (!username.value.trim() || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  error.value = ''
  try {
    const result = await auth.login(username.value.trim(), password.value)
    await router.replace(result.user.mustChangePassword ? '/change-password' : '/assistant')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-page { min-height: 100vh; display: grid; place-items: center; background: var(--bg); padding: 20px; }
.auth-card { width: 380px; }
.auth-brand { display: flex; align-items: center; gap: 9px; font-size: 16px; font-weight: 650; margin-bottom: 24px; }
.auth-mark { width: 28px; height: 28px; border-radius: 8px; background: var(--primary); color: #fff; display: grid; place-items: center; font-size: 14px; }
.auth-title { font-size: 18px; font-weight: 650; margin-bottom: 14px; }
.auth-error { color: #d35b52; font-size: 12.5px; margin-bottom: 10px; line-height: 1.5; }
.auth-submit { width: 100%; }
</style>
