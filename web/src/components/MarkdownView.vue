<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'

const props = defineProps<{ text: string }>()
const emit = defineEmits<{ (e: 'cite', index: number): void }>()

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

// 把 [1] 这类引用编号渲染成可点击角标，而不是普通正文字符。
// 用 inline rule 而不是渲染后正则替换，避免误伤链接地址、代码块里的内容。
md.inline.ruler.after('text', 'cite_ref', (state: any, silent: boolean) => {
  const pos = state.pos
  if (state.src[pos] !== '[') return false
  const m = /^\[(\d{1,3})\]/.exec(state.src.slice(pos))
  if (!m) return false
  if (!silent) {
    const token = state.push('cite_ref', '', 0)
    token.content = m[1]
  }
  state.pos += m[0].length
  return true
})
md.renderer.rules.cite_ref = (tokens: any[], idx: number) => {
  const n = Number(tokens[idx].content)
  return `<sup class="md-cite" data-cite="${n}" title="查看引用 ${n}">${n}</sup>`
}

const html = computed(() => md.render(props.text || ''))

function onClick(e: MouseEvent) {
  const el = (e.target as HTMLElement | null)?.closest?.('sup[data-cite]') as HTMLElement | null
  if (!el) return
  const n = Number(el.dataset.cite)
  if (n > 0) emit('cite', n - 1)
}
</script>

<template>
  <div class="md-body" @click="onClick" v-html="html" />
</template>

<style scoped>
.md-body { line-height: 1.8; word-break: break-word; }
.md-body :deep(> :first-child) { margin-top: 0; }
.md-body :deep(> :last-child) { margin-bottom: 0; }
.md-body :deep(p) { margin: 0 0 9px; }
.md-body :deep(strong) { color: var(--text); font-weight: 650; }
.md-body :deep(h1), .md-body :deep(h2), .md-body :deep(h3),
.md-body :deep(h4) { margin: 13px 0 6px; line-height: 1.5; font-weight: 650; color: var(--text); }
.md-body :deep(h1) { font-size: 17px; }
.md-body :deep(h2) { font-size: 15.5px; }
.md-body :deep(h3), .md-body :deep(h4) { font-size: 14.5px; }
.md-body :deep(ul), .md-body :deep(ol) { margin: 5px 0 10px; padding-left: 21px; }
.md-body :deep(li) { margin: 3px 0; }
.md-body :deep(blockquote) { margin: 9px 0; padding: 4px 11px; border-left: 3px solid var(--border-strong); color: var(--sub); }
.md-body :deep(code) { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12.5px; background: var(--surface-soft); border: 1px solid var(--border); border-radius: 4px; padding: 1px 4px; }
.md-body :deep(pre) { margin: 9px 0; padding: 10px 12px; overflow: auto; background: var(--surface-soft); border: 1px solid var(--border); border-radius: 7px; }
.md-body :deep(pre code) { border: 0; padding: 0; background: transparent; }
.md-body :deep(a) { color: var(--primary-dark); text-decoration: underline; }
.md-body :deep(table) { border-collapse: collapse; margin: 9px 0; max-width: 100%; }
.md-body :deep(th), .md-body :deep(td) { border: 1px solid var(--border); padding: 5px 8px; }

.md-body :deep(.md-cite) {
  display: inline-block;
  margin-left: 2px;
  padding: 0 4px;
  font-size: 10.5px;
  font-weight: 600;
  line-height: 1.6;
  color: var(--primary-dark);
  background: var(--primary-soft);
  border-radius: 4px;
  cursor: pointer;
  vertical-align: super;
}
.md-body :deep(.md-cite:hover) { background: #d6e2fd; }
</style>
