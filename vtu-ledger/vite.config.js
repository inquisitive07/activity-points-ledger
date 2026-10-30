import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Port 5173 is the origin the API allows through CORS (CORS_ORIGINS); strictPort fails fast instead of drifting.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
});
