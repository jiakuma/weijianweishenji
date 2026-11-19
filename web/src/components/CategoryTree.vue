<template>
  <div class="category-pane">
    <div class="pane-head">
      <span>知识分类</span>
    </div>

    <div class="cat-row" :class="{ 'is-active': !store.currentCategory }" @click="store.selectCategory('')">
      <span class="cat-caret" />
      <el-icon :size="15"><FolderOpened /></el-icon>
      <span class="cat-name">全部资料</span>
      <span class="cat-count">{{ store.total }}</span>
    </div>

    <div
      v-for="item in flat"
      :key="item.node.id"
      class="cat-row"
      :class="{ 'is-active': item.node.directOf
        ? (store.directOnly && store.currentCategory === item.node.directOf)
        : (!store.directOnly && store.currentCategory === item.node.name) }"
      :style="{ paddingLeft: `${8 + item.depth * 14}px` }"
      @click="item.node.directOf ? store.selectDirect(item.node.directOf) : store.selectCategory(item.node.name)"
    >
      <span class="cat-caret" @click.stop="toggle(item.node.id)">
        <el-icon v-if="item.node.children.length" :size="11">
          <ArrowRight v-if="!expanded.has(item.node.id)" />
          <ArrowDown v-else />
        </el-icon>
      </span>
      <el-icon :size="15"><Folder /></el-icon>
      <span class="cat-name" :title="item.node.name">{{ item.node.name }}</span>
      <el-button
        v-if="canDelete(item.node)"
        class="cat-del"
        text
        size="small"
        title="删除分类"
        @click.stop="deleteCat(item.node)"
      >
        <el-icon :size="13"><Delete /></el-icon>
      </el-button>
      <span class="cat-count">{{ item.node.count }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowDown, ArrowRight, Delete, Folder, FolderOpened } from '@element-plus/icons-vue'
import { api } from '@/api'
import { useKnowledgeStore } from '@/stores/knowledge'
import type { CategoryNode } from '@/types'

const RESERVED = '未分类'

const store = useKnowledgeStore()
const expanded = ref<Set<string>>(new Set())

const flat = computed(() => {
  const out: Array<{ node: CategoryNode; depth: number }> = []
  const walk = (nodes: CategoryNode[], depth: number) => {
    for (const node of nodes) {
      out.push({ node, depth })
      if (node.children.length && expanded.value.has(node.id)) {
        walk(node.children, depth + 1)
        if ((node.directCount ?? 0) > 0) {
          out.push({
            node: {
              id: `__direct__${node.id}`,
              name: '本级文件',
              parentId: node.id,
              orderIndex: 0,
              count: node.directCount ?? 0,
              children: [],
              directOf: node.name,
            },
            depth: depth + 1,
          })
        }
      }
    }
  }
  walk(store.tree, 0)
  return out
})

function canDelete(node: CategoryNode): boolean {
  return !node.id.startsWith('__direct__') && node.name !== RESERVED
}

function toggle(id: string) {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}

function collectNames(nodes: CategoryNode[], acc: Set<string> = new Set()): Set<string> {
  for (const n of nodes) {
    acc.add(n.name)
    if (n.children.length) collectNames(n.children, acc)
  }
  return acc
}

async function deleteCat(node: CategoryNode) {
  try {
    await ElMessageBox.confirm(
      `删除分类「${node.name}」及其下全部资料？资料将从系统移除（磁盘原始文件保留，可重新导入）。此操作不可恢复。`,
      '删除分类',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await api.deleteCategory(node.id)
  await Promise.all([store.loadCategories(), store.loadDocs(), store.loadStorage()])
  const names = collectNames(store.tree)
  if (store.currentCategory && !names.has(store.currentCategory)) {
    store.selectCategory('')
  }
  ElMessage.success('分类已删除')
}
</script>

<style scoped>
.cat-del {
  margin: 0 2px 0 auto;
  padding: 0 2px;
  color: var(--el-text-color-secondary);
  visibility: hidden;
}
.cat-row:hover .cat-del {
  visibility: visible;
}
.cat-del:hover {
  color: var(--el-color-danger);
}
</style>
