// 用 CDP 截取产品手册所需的真实界面截图（走真实登录表单）
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PORT = 9223
const APP = 'http://127.0.0.1:5280'
const OUT = 'E:/审计工作平台/docs/screenshots'
const PROFILE = 'C:/Users/wwt29/AppData/Local/Temp/cprof_manual'

fs.mkdirSync(OUT, { recursive: true })
try { fs.rmSync(PROFILE, { recursive: true, force: true }) } catch {}

const chrome = spawn(CHROME, [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${PROFILE}`,
  '--window-size=1440,900', 'about:blank',
], { stdio: 'ignore' })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function getWs() {
  for (let i = 0; i < 40; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json()
      const page = list.find((t) => t.type === 'page')
      if (page) return page.webSocketDebuggerUrl
    } catch {}
    await sleep(250)
  }
  throw new Error('Chrome CDP 未就绪')
}

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map() }
  async connect() {
    return new Promise((resolve, reject) => {
      this.ws.addEventListener('open', resolve)
      this.ws.addEventListener('error', reject)
      this.ws.addEventListener('message', (e) => {
        const m = JSON.parse(e.data)
        if (m.id && this.pending.has(m.id)) {
          this.pending.get(m.id)(m)
          this.pending.delete(m.id)
        }
      })
    })
  }
  send(method, params = {}) {
    const id = ++this.id
    return new Promise((resolve) => {
      this.pending.set(id, resolve)
      this.ws.send(JSON.stringify({ id, method, params }))
    })
  }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })
    return r.result?.result?.value
  }
  async nav(url) {
    await this.send('Page.navigate', { url })
    await sleep(1800)
  }
  async shot(name) {
    const r = await this.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
    const data = r.result?.data
    if (data) {
      fs.writeFileSync(path.join(OUT, name + '.png'), Buffer.from(data, 'base64'))
      console.log('  screenshot:', name)
    }
  }
}

async function waitFor(cdp, expr, timeout = 12000) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeout) {
    try { if (await cdp.eval(expr)) return true } catch {}
    await sleep(300)
  }
  return false
}

const wsUrl = await getWs()
const ws = new WebSocket(wsUrl)
const cdp = new CDP(ws)
await cdp.connect()
await cdp.send('Page.enable')
await cdp.send('Runtime.enable')
await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false })

// 1) 登录页
await cdp.nav(`${APP}/login`)
await waitFor(cdp, "!!document.querySelector('input')")
await cdp.shot('01-login')

// 2) 真实登录
const inputs = await cdp.eval("Array.from(document.querySelectorAll('input')).map(i=>({ph:i.placeholder,type:i.type}))")
console.log('登录表单 inputs:', JSON.stringify(inputs))
await cdp.eval(`(() => {
  const ins = Array.from(document.querySelectorAll('input'))
  const user = ins.find(i => (i.placeholder||'').includes('用户') || i.type==='text') || ins[0]
  const pw = ins.find(i => i.type==='password') || ins[1]
  const set = (el,v) => { const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; s.call(el,v); el.dispatchEvent(new Event('input',{bubbles:true})) }
  if(user) set(user,'admin')
  if(pw) set(pw,'Admin@12345')
  return true
})()`)
await sleep(300)
// 点登录按钮
await cdp.eval(`(() => {
  const b = Array.from(document.querySelectorAll('button')).find(x => (x.innerText||'').includes('登录'))
  if (b) b.click()
  return !!b
})()`)
await sleep(2500)
console.log('登录后 URL:', await cdp.eval('location.href'))

// 首次登录可能强制改密，这里若仍在 login/change-password 就停下报告
let href = await cdp.eval('location.href')
if (href.includes('login') || href.includes('change-password')) {
  console.log('!! 登录未完成，停在:', href)
  console.log('页面文本:', (await cdp.eval('document.body.innerText') || '').slice(0, 500))
}

const pages = [
  ['02-assistant', '/assistant'],
  ['03-library', '/library'],
  ['04-issues', '/issues'],
  ['05-import', '/import'],
  ['06-conflict', '/conflict'],
  ['07-contract', '/contract'],
  ['08-workpaper', '/workpaper'],
  ['09-users', '/users'],
  ['10-feedback', '/feedback'],
]

for (const [name, route] of pages) {
  await cdp.nav(`${APP}${route}`)
  await sleep(800)
  await cdp.shot(name)
}

ws.close()
chrome.kill('SIGKILL')
console.log('完成，截图目录:', OUT)
