import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // 5173 is the API's default allowed CORS origin, so keep it pinned rather
  // than letting Vite pick the next free port and silently break every call.
  server: { port: 5173, strictPort: true },
})
