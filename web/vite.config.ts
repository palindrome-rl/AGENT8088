import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

const backend = process.env.AGENT8088_WEB_BACKEND || 'http://127.0.0.1:8180'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5180,
    host: '127.0.0.1',
    proxy: {
      '/api': backend,
      '/ws': { target: backend.replace('http', 'ws'), ws: true },
    },
  },
})
