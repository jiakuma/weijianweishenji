<template>
  <div>
    <div class="page-head">
      <el-button type="primary" style="margin-left: auto" :loading="saving" @click="save">保存设置</el-button>
    </div>

    <div class="settings-grid">
      <div>
        <div class="card card-pad" style="margin-bottom: 14px">
          <div class="section-title">模型配置</div>
          <div style="font-size: 12.5px; line-height: 1.75; color: #5f5e5a; background: #f5f7fa; border-radius: 8px; padding: 10px 12px; margin-bottom: 14px">
            当前接入方式：<b>DeepSeek 官方 API</b>（OpenAI 兼容）。地址 <b>https://api.deepseek.com</b>，
            模型 <b>deepseek-flash</b> 即 DeepSeek-V4.1-Flash，官方地址不带 /v1。
            Key 在 platform.deepseek.com 申请，只存本机数据库、不写入代码。
            若需回退原内网代理，把地址改回 https://dmxwg.intra.yiducloud.cn/litellm/v1 即可。
          </div>
          <el-form label-width="118px" label-position="left">
            <el-form-item label="LLM API 地址">
              <el-input v-model="form.llmBaseUrl" placeholder="https://api.deepseek.com" clearable />
            </el-form-item>
            <el-form-item label="API Key">
              <el-input v-model="form.llmApiKey" placeholder="sk-...（DeepSeek 平台申请）" show-password clearable />
            </el-form-item>
            <el-form-item label="模型名称">
              <el-input v-model="form.llmModel" placeholder="deepseek-flash" clearable />
            </el-form-item>
            <el-form-item label="思考模式">
              <div style="display: flex; align-items: center; gap: 10px">
                <el-switch v-model="form.llmThinking" />
                <span class="sub" style="font-size: 12px">仅知识问答生效；条款抽取、比对等结构化任务保持关闭以保证 JSON 稳定</span>
              </div>
            </el-form-item>
            <el-form-item label="Temperature">
              <el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" :precision="1" />
            </el-form-item>
            <el-form-item label="Max Tokens">
              <el-input-number v-model="form.maxTokens" :min="256" :max="32768" :step="256" />
            </el-form-item>
            <el-form-item label="Embedding 地址">
              <el-input v-model="form.embeddingBaseUrl" placeholder="可选" clearable />
            </el-form-item>
            <el-form-item label="Embedding 模型">
              <el-input v-model="form.embeddingModel" placeholder="可选" clearable />
            </el-form-item>
          </el-form>
          <div style="display: flex; align-items: center; gap: 8px; margin-top: 4px; flex-wrap: wrap">
            <el-button type="primary" :loading="saving" @click="save">保存设置</el-button>
            <el-button :loading="testing" @click="testModel">测试模型连接</el-button>
            <el-button size="small" @click="useOfficialPreset">填入官方配置</el-button>
            <span v-if="testResult" class="sub" :style="{ color: testResult.ok ? '#2c9473' : '#c15b52', fontSize: '12.5px' }">
              {{ testResult.message }}
            </span>
          </div>
        </div>

        <div class="card card-pad" style="margin-bottom: 14px">
          <div class="section-title">访问令牌</div>
          <el-form label-width="118px" label-position="left">
            <el-form-item label="令牌">
              <el-input v-model="tokenInput" show-password clearable placeholder="留空表示不启用" />
            </el-form-item>
          </el-form>
          <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
            <el-button size="small" @click="applyToken">应用</el-button>
            <el-button size="small" type="primary" :loading="tokenSaving" @click="saveToken">保存到后端</el-button>
            <el-button size="small" :disabled="!form.accessTokenSet" @click="clearToken">清除</el-button>
            <span class="sub" style="font-size: 12px">{{ tokenStatus }}</span>
          </div>
        </div>

        <div class="card card-pad" style="margin-bottom: 14px">
          <div class="section-title">检索评测</div>
          <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
            <el-input-number v-model="evalCount" :min="2" :max="30" size="small" />
            <el-button size="small" :loading="evalBuilding" @click="buildEval">生成评估集</el-button>
            <el-button size="small" type="primary" :loading="evalRunning" @click="runEval">跑评测</el-button>
            <el-button size="small" :disabled="!evalTotal" @click="clearEval">清空</el-button>
            <span class="sub" style="font-size: 12px">{{ evalTotal }} 条样本</span>
          </div>
          <el-table v-if="evalRuns.length" :data="evalRuns" size="small" style="margin-top: 12px">
            <el-table-column prop="createdAt" label="时间" width="130" />
            <el-table-column label="Hit Rate@K" width="110">
              <template #default="{ row }">{{ (row.hitRate * 100).toFixed(1) }}%</template>
            </el-table-column>
            <el-table-column label="MRR" width="90">
              <template #default="{ row }">{{ row.mrr.toFixed(3) }}</template>
            </el-table-column>
            <el-table-column prop="total" label="样本" width="65" />
            <el-table-column prop="costMs" label="耗时ms" width="85" />
          </el-table>
          <div v-if="evalMisses.length" class="eval-misses">
            <div class="sub" style="font-size: 12px; margin-top: 10px">未命中样本</div>
            <div v-for="(m, mi) in evalMisses" :key="mi" style="font-size: 11.5px; color: var(--sub); line-height: 1.6; word-break: break-all">{{ m.question }} → 期望《{{ m.expectedDoc }}》</div>
          </div>
        </div>

        <div class="card card-pad">
          <div class="section-title">解析配置</div>
          <div class="setting-row">
            <span class="setting-label">支持的文件类型</span>
            <el-checkbox-group v-model="form.supportedFormats">
              <el-checkbox v-for="f in allFormats" :key="f" :value="f">{{ f }}</el-checkbox>
            </el-checkbox-group>
          </div>
          <div class="setting-row"><span class="setting-label">自动生成摘要</span><el-switch v-model="form.autoSummary" /></div>
          <div class="setting-row"><span class="setting-label">自动分类</span><el-switch v-model="form.autoClassify" /></div>
          <div class="setting-row"><span class="setting-label">自动切片</span><el-switch v-model="form.autoChunk" /></div>
          <div class="setting-row">
            <span class="setting-label">失败重试次数</span>
            <el-input-number v-model="form.retryCount" :min="0" :max="5" size="small" />
          </div>
          <div class="setting-row">
            <span class="setting-label">知乎 Cookie</span>
            <div style="display: flex; gap: 8px; align-items: flex-start">
              <el-input v-model="form.zhihuCookie" type="textarea" :rows="2" resize="none" placeholder="Cookie / cURL / 请求头" style="width: 260px" />
              <el-button size="small" :loading="loginWaiting" @click="startZhihuLogin">登录获取</el-button>
            </div>
          </div>
        </div>
      </div>

      <div class="card card-pad">
        <div class="section-title">存储管理</div>
        <div class="storage-line"><span class="sub">原始文件位置</span><span class="muted" style="font-size: 11.5px; word-break: break-all; text-align: right; max-width: 62%">{{ store.storage?.originalsDir }}</span></div>
        <div class="storage-line"><span class="sub">解析文件位置</span><span class="muted" style="font-size: 11.5px; word-break: break-all; text-align: right; max-width: 62%">{{ store.storage?.parsedDir }}</span></div>
        <div class="storage-line"><span class="sub">数据库位置</span><span class="muted" style="font-size: 11.5px; word-break: break-all; text-align: right; max-width: 62%">{{ store.storage?.databasePath }}</span></div>
        <div class="storage-line"><span class="sub">原始文件</span><span>{{ store.storage?.originalFiles ?? 0 }} 个 · {{ store.storage?.originalMb ?? 0 }} MB</span></div>
        <div class="storage-line"><span class="sub">解析文本</span><span>{{ store.storage?.parsedMb ?? 0 }} MB</span></div>
        <div class="storage-line"><span class="sub">数据库</span><span>{{ store.storage?.databaseMb ?? 0 }} MB</span></div>
        <div class="storage-line"><span class="sub">合计占用</span><span style="font-weight: 600">{{ store.storage?.totalMb ?? 0 }} MB</span></div>

        <el-input v-model="newLocation" placeholder="新的原始文件目录" style="margin-top: 16px" clearable />
        <el-button style="margin-top: 8px" :loading="moving" @click="changeLocation">修改存储位置</el-button>

        <div class="section-title" style="margin-top: 22px">备份与恢复</div>
        <div style="display: flex; gap: 8px">
          <el-button :loading="backing" @click="createBackup">创建备份</el-button>
          <el-button :disabled="!backups.length" @click="restore">恢复选中备份</el-button>
        </div>
        <el-radio-group v-if="backups.length" v-model="selectedBackup" style="margin-top: 12px; display: flex; flex-direction: column; align-items: flex-start; gap: 6px">
          <el-radio v-for="b in backups" :key="b.name" :value="b.name">
            {{ b.createdAt }} · {{ b.sizeMb }} MB
          </el-radio>
        </el-radio-group>
        <div v-else class="muted" style="font-size: 12.5px; margin-top: 12px">暂无备份</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, getAccessToken, setAccessToken } from '@/api'
import { useKnowledgeStore } from '@/stores/knowledge'
import type { BackupItem, EvalMiss, EvalRunItem, Settings } from '@/types'

const store = useKnowledgeStore()
const form = reactive<Settings>({ ...store.settings })
const allFormats = ['PDF', 'PPTX', 'DOCX', 'MD', 'TXT', '图片']
const saving = ref(false)
const testing = ref(false)
const testResult = ref<{ ok: boolean; message: string } | null>(null)
const newLocation = ref('')
const moving = ref(false)
const backing = ref(false)
const backups = ref<BackupItem[]>([])
const selectedBackup = ref('')
const loginWaiting = ref(false)
const tokenInput = ref('')
const tokenSaving = ref(false)
const evalCount = ref(8)
const evalBuilding = ref(false)
const evalRunning = ref(false)
const evalTotal = ref(0)
const evalRuns = ref<EvalRunItem[]>([])
const evalMisses = ref<EvalMiss[]>([])
let loginTimer = 0

async function loadEval() {
  try {
    const [q, r] = await Promise.all([api.listEvalQueries(), api.listEvalRuns()])
    evalTotal.value = q.total
    evalRuns.value = r
    evalMisses.value = r[0]?.misses || []
  } catch {
    // 评测数据为空或后端不可用时不阻塞设置页
  }
}

async function buildEval() {
  evalBuilding.value = true
  try {
    const res = await api.buildEvalSet(evalCount.value)
    if (!res.ok) {
      ElMessage.warning(res.message || '生成失败')
      return
    }
    ElMessage.success(`新增 ${res.created} 条评估问题`)
    await loadEval()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    evalBuilding.value = false
  }
}

async function runEval() {
  evalRunning.value = true
  try {
    const res = await api.runEval(5, '混合')
    if (!res.ok) {
      ElMessage.warning(res.message || '评测失败')
      return
    }
    evalMisses.value = res.misses || []
    await loadEval()
    ElMessage.success(`Hit Rate@5 ${(res.hitRate * 100).toFixed(1)}% · MRR ${res.mrr.toFixed(3)}`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    evalRunning.value = false
  }
}

async function clearEval() {
  try {
    await api.clearEvalQueries()
    evalTotal.value = 0
    ElMessage.success('评估集已清空')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

const tokenStatus = computed(() => {
  if (!form.accessTokenSet) return '未启用（仅本机可写）'
  return getAccessToken() ? '已在本机应用' : '后端已启用，本机未应用'
})

function applyToken() {
  setAccessToken(tokenInput.value.trim())
  tokenInput.value = ''
  ElMessage.success(getAccessToken() ? '令牌已在本机应用' : '已清除本机令牌')
}

async function saveToken() {
  const value = tokenInput.value.trim()
  tokenSaving.value = true
  try {
    const updated = await api.saveSettings({ accessToken: value })
    setAccessToken(value)
    Object.assign(form, updated)
    tokenInput.value = ''
    ElMessage.success(value ? '访问令牌已启用' : '访问令牌已清除')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    tokenSaving.value = false
  }
}

async function clearToken() {
  tokenSaving.value = true
  try {
    const updated = await api.saveSettings({ accessToken: '' })
    setAccessToken('')
    Object.assign(form, updated)
    tokenInput.value = ''
    ElMessage.success('访问令牌已清除')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    tokenSaving.value = false
  }
}

async function startZhihuLogin() {
  if (loginWaiting.value) return
  loginWaiting.value = true
  try {
    const res = await api.zhihuLoginStart()
    if (!res.ok) {
      ElMessage.warning(res.message)
      loginWaiting.value = false
      return
    }
    if (loginTimer) window.clearInterval(loginTimer)
    loginTimer = window.setInterval(async () => {
      let st: { state: string; message: string }
      try {
        st = await api.zhihuLoginStatus()
      } catch {
        return
      }
      if (st.state === 'waiting' || st.state === 'idle') return
      window.clearInterval(loginTimer)
      loginTimer = 0
      loginWaiting.value = false
      if (st.state === 'ok') {
        await store.loadSettings()
        ElMessage.success('知乎登录态已同步')
      } else {
        ElMessage.warning(st.message || '登录未完成')
      }
    }, 1500)
  } catch {
    loginWaiting.value = false
    ElMessage.error('启动登录窗口失败')
  }
}

onBeforeUnmount(() => {
  if (loginTimer) window.clearInterval(loginTimer)
})

watch(() => store.settings, (value) => Object.assign(form, value), { deep: true })

async function loadBackups() {
  backups.value = await api.listBackups()
  if (!selectedBackup.value && backups.value.length) selectedBackup.value = backups.value[0].name
}

function useOfficialPreset() {
  form.llmBaseUrl = 'https://api.deepseek.com'
  form.llmModel = 'deepseek-flash'
  ElMessage.success('已填入 DeepSeek 官方配置，请填写 API Key 后点「保存设置」')
}

async function save() {
  const rawCookie = (form.zhihuCookie || '').trim()
  if (rawCookie && rawCookie !== '••••••••' && /request url|status code|set-cookie|content-security-policy/i.test(rawCookie)) {
    ElMessage.error('这是响应头，请复制 Request Headers 里的 cookie 行，或右键请求 → Copy as cURL')
    return
  }
  saving.value = true
  try {
    const updated = await api.saveSettings({
      llmBaseUrl: form.llmBaseUrl.trim(),
      llmApiKey: form.llmApiKey,
      zhihuCookie: form.zhihuCookie,
      llmModel: form.llmModel.trim(),
      llmThinking: Boolean(form.llmThinking),
      temperature: Number(form.temperature),
      maxTokens: Number(form.maxTokens),
      embeddingBaseUrl: form.embeddingBaseUrl.trim(),
      embeddingModel: form.embeddingModel.trim(),
      supportedFormats: form.supportedFormats,
      autoSummary: form.autoSummary,
      autoClassify: form.autoClassify,
      autoChunk: form.autoChunk,
      retryCount: Number(form.retryCount),
    })
    Object.assign(form, updated)
    ElMessage.success('设置已保存')
  } finally {
    saving.value = false
  }
}

async function testModel() {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await api.testModel({
      baseUrl: form.llmBaseUrl.trim(),
      model: form.llmModel.trim(),
      apiKey: form.llmApiKey && form.llmApiKey !== '••••••••' ? form.llmApiKey : undefined,
    })
    // 防呆：测试用的是表单里的临时 Key，测试成功 ≠ 已保存
    if (testResult.value.ok && form.llmApiKey && form.llmApiKey !== '••••••••') {
      ElMessage.warning('连接成功，但 Key 还未保存，请点「保存设置」使其长期生效')
    }
  } finally {
    testing.value = false
  }
}

async function changeLocation() {
  if (!newLocation.value.trim()) return
  moving.value = true
  try {
    const res = await api.changeLocation(newLocation.value.trim())
    if (!res.ok) {
      ElMessage.error(res.message)
      return
    }
    ElMessage.success(res.message)
    await store.loadStorage()
  } finally {
    moving.value = false
  }
}

async function createBackup() {
  backing.value = true
  try {
    const res = await api.createBackup()
    ElMessage.success(`已创建备份 ${res.name}`)
    await loadBackups()
    await store.loadStorage()
  } finally {
    backing.value = false
  }
}

async function restore() {
  if (!selectedBackup.value) return
  try {
    await ElMessageBox.confirm(
      `用 ${selectedBackup.value} 覆盖当前数据库？当前数据会被备份内容替换。`,
      '恢复备份',
      { type: 'warning', confirmButtonText: '恢复', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  const res = await api.restoreBackup(selectedBackup.value)
  ElMessage[res.ok ? 'success' : 'error'](res.message)
  if (res.ok) await store.loadAll()
}

onMounted(() => {
  void loadBackups()
  void loadEval()
})
</script>
