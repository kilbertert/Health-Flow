import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite 配置：开发服务器运行在 5173，并把 /api、/health、/ready 请求代理到 HealthFlow FastAPI 服务
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8127',
        changeOrigin: true,
      },
      // 后端健康/就绪检查挂在根路径（非 /api 前缀）
      '/health': {
        target: 'http://127.0.0.1:8127',
        changeOrigin: true,
      },
      '/ready': {
        target: 'http://127.0.0.1:8127',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // 第三方库拆分为独立 chunk，优化首屏加载
        manualChunks: {
          react: ['react', 'react-dom'],
          antd: ['antd', '@ant-design/icons'],
        },
      },
    },
  },
});
