import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  base: '/',
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    // 构建进本项目的 static 目录（FastAPI 直接服务）
    outDir: '../static',
    // 每次构建先清空 static，避免旧的打包文件越积越多（favicon.ico 放在 web/public，会被一起复制过去）
    emptyOutDir: true,
    sourcemap: false,
    rolldownOptions: {
      output: {
        // 合并成少数几个文件：服务器限制每个 IP 同时 25 个连接（HTTP/2 下每个请求都算），
        // 拆成几十个小文件时偶尔会有文件被拒（503），页面白屏
        advancedChunks: {
          groups: [
            { name: 'vendor', test: /node_modules/, priority: 20 },
            // 公共代码（组件库、接口、工具）合成一个；页面本身仍按路由懒加载
            { name: 'shared', test: /\/src\/(components|api|lib|stores|composables|types)\//, priority: 10 },
          ],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8800',
        changeOrigin: true,
        // SSE 相关：透传流式响应，禁用缓冲
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['cache-control'] = 'no-cache'
            proxyRes.headers['x-accel-buffering'] = 'no'
            delete proxyRes.headers['content-length']
          })
        },
      },
    },
  },
})
