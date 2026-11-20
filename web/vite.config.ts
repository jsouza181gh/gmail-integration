import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The API only accepts writes whose Origin equals APP_ORIGIN, so the dev proxy
// rewrites the browser's Origin (http://localhost:5173) to that value.
const appOrigin = process.env.APP_ORIGIN ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        headers: { origin: appOrigin },
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', 'test/**/*.test.{ts,tsx}'],
  },
})
