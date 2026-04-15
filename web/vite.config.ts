import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:57381',
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/ws/sessions': { target: 'ws://localhost:57381', ws: true },
      '/ws': { target: 'ws://localhost:57381', ws: true },
    },
  },
})
