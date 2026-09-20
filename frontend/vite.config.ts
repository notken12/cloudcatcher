/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Same-origin dev proxy to the camera backend (FastAPI) if you'd rather not set VITE_API_BASE.
    proxy: {
      '/feed': 'http://localhost:8000',
      '/stream': 'http://localhost:8000',
      '/events': 'http://localhost:8000',
      '/proxy': 'http://localhost:8000',
      '/push': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
  },
})
