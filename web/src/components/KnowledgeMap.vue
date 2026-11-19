<template>
  <div v-show="model" class="map-float">
    <div class="map-head">
      <el-icon :size="17"><Share /></el-icon>
      <span>知识地图</span>
      <span class="muted" style="font-size: 12px; font-weight: 400">{{ store.total }} 篇资料</span>
      <div style="margin-left: auto; display: flex; gap: 4px">
        <el-button text size="small" @click="zoomBy(1.2)"><el-icon><ZoomIn /></el-icon></el-button>
        <el-button text size="small" @click="zoomBy(1 / 1.2)"><el-icon><ZoomOut /></el-icon></el-button>
        <el-button text size="small" @click="resetView"><el-icon><Refresh /></el-icon></el-button>
        <el-button text size="small" @click="model = false"><el-icon><Close /></el-icon></el-button>
      </div>
    </div>

    <div ref="bodyEl" class="map-body">
      <svg
        ref="svgEl"
        :viewBox="`0 0 ${boxW} ${boxH}`"
        :style="{ width: '100%', height: '100%', display: 'block', cursor: dragging ? 'grabbing' : 'grab', touchAction: 'none' }"
        @wheel.prevent="onWheel"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointerleave="onPointerUp"
        @pointercancel="onPointerUp"
        @click.capture="onClickCapture"
      >
        <g :transform="`translate(${tx} ${ty}) scale(${k})`">
          <path
            v-for="(line, i) in lines"
            :key="`l${i}`"
            :d="line.d"
            fill="none"
            :stroke="line.color"
            stroke-width="1.6"
            opacity="0.75"
          />
          <g class="map-node" @click="model = false">
            <rect
              :x="g.rootX" :y="rootY - g.nodeH / 2" :width="g.rootW" :height="g.nodeH"
              :rx="g.nodeH / 2" fill="#3b6ff6"
            />
            <text :x="g.rootX + g.rootW / 2" :y="rootY - 1" text-anchor="middle" fill="#fff" :font-size="g.rootFont" font-weight="600">我的知识库</text>
            <text :x="g.rootX + g.rootW / 2" :y="rootY + 16" text-anchor="middle" fill="#dce8ff" :font-size="g.rootFont - 3">({{ store.total }})</text>
          </g>
          <g v-for="row in rows" :key="row.node.id" class="map-node" @click="locate(row.node)">
            <rect
              :x="row.x" :y="row.y" :width="g.w" :height="g.nodeH" :rx="g.nodeH / 2"
              :fill="row.color.bg" stroke="#e6edf8" stroke-width="1"
              class="map-node-box"
            />
            <circle :cx="row.x + 20" :cy="row.y + g.nodeH / 2" r="9" :fill="row.color.dot" />
            <text :x="row.x + 36" :y="row.y + g.nodeH / 2 + 4.5" fill="#1b2a41" :font-size="g.font">{{ label(row.node.name) }}</text>
            <text :x="row.x + g.w - 14" :y="row.y + g.nodeH / 2 + 4.5" text-anchor="end" fill="#5b6b84" :font-size="g.countFont">{{ row.node.count }}</text>
            <g v-if="row.node.children.length" @click.stop="toggle(row.node.id)">
              <circle :cx="row.x + g.w + 12" :cy="row.y + g.nodeH / 2" r="10" fill="#fff" stroke="#d7e2f5" />
              <text :x="row.x + g.w + 12" :y="row.y + g.nodeH / 2 + 4.5" text-anchor="middle" fill="#3b6ff6" :font-size="g.countFont + 1">
                {{ expanded.has(row.node.id) ? '−' : '+' }}
              </text>
            </g>
          </g>
        </g>
      </svg>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Close, Refresh, Share, ZoomIn, ZoomOut } from '@element-plus/icons-vue'
import { useKnowledgeStore } from '@/stores/knowledge'
import type { CategoryNode } from '@/types'

const model = defineModel<boolean>({ required: true })
const store = useKnowledgeStore()
const router = useRouter()
const expanded = ref<Set<string>>(new Set())
const bodyEl = ref<HTMLElement | null>(null)
const svgEl = ref<SVGSVGElement | null>(null)
const boxW = ref(900)
const boxH = ref(380)

const k = ref(1)
const tx = ref(0)
const ty = ref(0)
const dragging = ref(false)
let drag: { x: number; y: number; tx: number; ty: number; sx: number; sy: number } | null = null
let moved = false

let observer: ResizeObserver | null = null
function measure() {
  if (!bodyEl.value || !model.value) return
  boxW.value = Math.max(320, bodyEl.value.clientWidth)
  boxH.value = Math.max(340, bodyEl.value.clientHeight)
}

const TONES = [
  { bg: '#eaf1ff', dot: '#3b6ff6', line: '#9db9fb' },
  { bg: '#f1edff', dot: '#8462c9', line: '#c3b1ee' },
  { bg: '#e9f7f0', dot: '#2c9473', line: '#9bd8bf' },
  { bg: '#fff3e8', dot: '#d08a3c', line: '#f0c79b' },
  { bg: '#fdeef0', dot: '#cc6a72', line: '#eeb3b9' },
  { bg: '#eaf4fb', dot: '#3f8fb5', line: '#a8d2e6' },
]

/** 内容坐标系：按容器真实像素布局，节点尺寸恒定 */
const g = computed(() => {
  const W = Math.max(900, boxW.value)
  const w = Math.min(300, Math.max(190, Math.round(W * 0.17)))
  return {
    W, row: 82, nodeH: 54, w,
    x1: Math.round(W * 0.26), x2: Math.round(W * 0.60),
    rootX: 44, rootW: 170, font: 15, countFont: 13, rootFont: 17,
    maxChars: Math.max(11, Math.floor((w - 70) / 15)),
  }
})

const rowCount = computed(() => {
  let n = 0
  for (const root of store.tree) {
    n += 1
    if (expanded.value.has(root.id)) n += root.children.length
  }
  return n
})
const contentH = computed(() => rowCount.value * g.value.row + 52)
const rootY = computed(() => contentH.value / 2)

function initialView() {
  const base = Math.min(1, boxW.value / g.value.W)
  return {
    k: base,
    tx: 40 - g.value.rootX * base,
    ty: boxH.value / 2 - rootY.value * base,
  }
}

function resetView() {
  const v = initialView()
  k.value = v.k
  tx.value = v.tx
  ty.value = v.ty
}

function zoomAt(px: number, py: number, factor: number) {
  const next = Math.min(2.5, Math.max(0.3, k.value * factor))
  if (next === k.value) return
  const ratio = next / k.value
  tx.value = px - (px - tx.value) * ratio
  ty.value = py - (py - ty.value) * ratio
  k.value = next
}

function onWheel(e: WheelEvent) {
  const rect = svgEl.value?.getBoundingClientRect()
  if (!rect || !rect.width || !rect.height) return
  const px = (e.clientX - rect.left) * (boxW.value / rect.width)
  const py = (e.clientY - rect.top) * (boxH.value / rect.height)
  zoomAt(px, py, e.deltaY < 0 ? 1.12 : 1 / 1.12)
}

function zoomBy(factor: number) {
  zoomAt(boxW.value / 2, boxH.value / 2, factor)
}

function onPointerDown(e: PointerEvent) {
  if (e.button !== 0) return
  const rect = svgEl.value?.getBoundingClientRect()
  drag = {
    x: e.clientX, y: e.clientY, tx: tx.value, ty: ty.value,
    sx: rect?.width ? boxW.value / rect.width : 1,
    sy: rect?.height ? boxH.value / rect.height : 1,
  }
  moved = false
}

function onPointerMove(e: PointerEvent) {
  if (!drag) return
  const dx = e.clientX - drag.x
  const dy = e.clientY - drag.y
  if (!moved && Math.abs(dx) + Math.abs(dy) > 4) {
    moved = true
    dragging.value = true
  }
  if (!moved) return
  tx.value = drag.tx + dx * drag.sx
  ty.value = drag.ty + dy * drag.sy
}

function onPointerUp() {
  drag = null
  dragging.value = false
  if (moved) window.setTimeout(() => { moved = false }, 0)
}

function onClickCapture(e: MouseEvent) {
  if (!moved) return
  e.stopPropagation()
  moved = false
}

function label(name: string) {
  const max = g.value.maxChars
  return name.length > max ? `${name.slice(0, max - 1)}…` : name
}

function toggle(id: string) {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}

const rows = computed(() => {
  const geo = g.value
  const out: Array<{ node: CategoryNode; x: number; y: number; color: typeof TONES[number]; depth: number }> = []
  let index = 0
  for (const root of store.tree) {
    out.push({ node: root, x: geo.x1, y: 26 + index * geo.row, color: TONES[index % TONES.length], depth: 0 })
    index += 1
    if (expanded.value.has(root.id)) {
      for (const child of root.children) {
        out.push({ node: child, x: geo.x2, y: 26 + index * geo.row, color: TONES[index % TONES.length], depth: 1 })
        index += 1
      }
    }
  }
  return out
})

const lines = computed(() => {
  const geo = g.value
  const result: Array<{ d: string; color: string }> = []
  for (const row of rows.value) {
    const y = row.y + geo.nodeH / 2
    if (row.depth === 0) {
      const x0 = geo.rootX + geo.rootW
      result.push({ d: `M ${x0} ${rootY.value} C ${x0 + 52} ${rootY.value}, ${row.x - 52} ${y}, ${row.x} ${y}`, color: row.color.line })
    } else {
      const parent = rows.value.find((r) => r.depth === 0 && r.node.children.some((c) => c.id === row.node.id))
      if (parent) {
        const x0 = parent.x + geo.w
        const y1 = parent.y + geo.nodeH / 2
        result.push({ d: `M ${x0} ${y1} C ${x0 + 52} ${y1}, ${row.x - 52} ${y}, ${row.x} ${y}`, color: row.color.line })
      }
    }
  }
  return result
})

/** 展开/折叠后根节点的内容坐标会变，补偿平移量保持根节点在屏幕上不动 */
watch(rowCount, (n, prev) => {
  ty.value -= ((n - prev) * g.value.row / 2) * k.value
})
watch(model, (open) => { if (open) void nextTick(() => { measure(); resetView() }) })

onMounted(() => {
  if (!bodyEl.value) return
  observer = new ResizeObserver(measure)
  observer.observe(bodyEl.value)
  measure()
  resetView()
})
onBeforeUnmount(() => observer?.disconnect())

function locate(node: CategoryNode) {
  store.selectCategory(node.name)
  model.value = false
  void router.push('/library')
}
</script>
