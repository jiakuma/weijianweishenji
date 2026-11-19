import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
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
  },
})
