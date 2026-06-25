import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// vite dev 代理后端地址：默认 taishan(8003)；用环境变量切换租户实例。
//   见 package.json 的 dev:taishankaifa2（→ 8004）。前端 .env.development 始终用 /api 同源前缀。
const proxyTarget = process.env.VITE_PROXY_TARGET || 'http://localhost:8003';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 4000,
    proxy: {
      // 浏览器走同源 /api/*，由 vite 在服务器侧转发到后端。
      // 这样无论浏览器是否与服务器同机都能工作（避免 http://localhost:<port> 对远程浏览器无效）。
      // 后端端口由上方 proxyTarget 决定（默认 taishan 8003；taishankaifa2 用 8004）。
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
});
