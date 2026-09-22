/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
  build: { outDir: 'dist' },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    // 钉死 NODE_ENV='test'：react 的 CJS 入口靠 process.env.NODE_ENV 决定加载
    // production 还是 development 构建，而 @testing-library/react 的 act() 在
    // production 构建里会直接抛错。CI / 别人的机器上 NODE_ENV 不可控，必须自己锁死。
    env: { NODE_ENV: 'test' },
  },
});
