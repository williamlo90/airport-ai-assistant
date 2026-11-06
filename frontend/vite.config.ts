import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // The bootstrap has no environment contract and never loads a local .env.
  envDir: false,
  server: {
    host: '127.0.0.1',
    port: mode === 'integrated' ? 5175 : 5173,
    strictPort: true,
    ...(mode === 'integrated' ? { hmr: false, ws: false } : {}),
  },
  preview: { host: '127.0.0.1', port: 5174, strictPort: true },
}));
