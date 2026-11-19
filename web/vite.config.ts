import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  plugins: [
    vue(),
    // 自动导入 Vue API 与 Element Plus 组件；显式 import 仍保留，不影响现有代码
    AutoImport({
      imports: ['vue', 'vue-router', 'pinia'],
      resolvers: [ElementPlusResolver()],
      dts: 'src/typings/auto-imports.d.ts',
    }),
    Components({
      resolvers: [ElementPlusResolver()],
      dts: 'src/typings/components.d.ts',
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5280,
    strictPort: true,
  },
  preview: {
    port: 5280,
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    // 不清空产物目录：本机回收站策略会拦截批量删除，覆盖写入即可
    emptyOutDir: false,
    chunkSizeWarningLimit: 1200,
    target: 'chrome80',
  },
})
