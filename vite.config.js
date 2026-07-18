import { defineConfig } from 'vite';

// 移除构建产物中的 crossorigin 属性（Electron file:// 协议不需要）
function removeCrossorigin() {
  return {
    name: 'remove-crossorigin',
    transformIndexHtml(html) {
      return html.replace(/ crossorigin/g, '');
    }
  };
}

export default defineConfig({
  root: 'renderer',
  base: './',
  plugins: [removeCrossorigin()],
  build: {
    outDir: '../dist/renderer',
    emptyOutDir: true,
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      input: 'renderer/index.html',
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/echarts')) return 'echarts';
          if (id.includes('node_modules/flatpickr')) return 'flatpickr';
          if (id.includes('node_modules/pinyin-pro')) return 'pinyin-pro';
        },
      },
    },
  },
  server: {
    port: 5173,
  },
});
