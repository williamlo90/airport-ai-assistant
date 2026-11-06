import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  envDir: false,
  test: {
    environment: 'jsdom',
    // Shared workstation: cap workers and avoid concurrent JSDOM files.
    maxWorkers: 2,
    fileParallelism: false,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    clearMocks: true,
    restoreMocks: true,
  },
});
