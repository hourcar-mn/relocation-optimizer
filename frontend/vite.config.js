import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // in production the Worker serves the app and routes /api/* to the container.
  // locally, proxy the same paths to the FastAPI server on :8000 so the app
  // code is identical in both places (and CORS never comes up).
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
