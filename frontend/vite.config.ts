/// <reference types="vitest/config" />
import path from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { tanstackRouter } from '@tanstack/router-plugin/vite'
import { playwright } from '@vitest/browser-playwright'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    tanstackRouter({
      target: 'react',
      autoCodeSplitting: true,
    }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    silent: 'passed-only',
    unstubEnvs: true,
    coverage: {
      // Report over all source so untested modules surface at 0% instead of
      // vanishing from the denominator. No `thresholds` yet: the floor is
      // report-only until the measured baseline is reviewed (see
      // docs/testing/STANDARD.md SS7).
      include: ['src/**/*.{js,jsx,ts,tsx}'],
      exclude: [
        'src/components/ui/**',
        'src/assets/**',
        'src/tanstack-table.d.ts',
        'src/routeTree.gen.ts',
        'src/test-utils/**',
        // Interim: route files are exercised by the L4 Playwright suite, which
        // does not exist yet. Remove this exclusion when it does (STANDARD SS3).
        'src/routes/**',
      ],
    },
    projects: [
      {
        extends: true,
        test: {
          name: 'browser',
          // The design-system rule test loads ESLint, which reaches for Node
          // built-ins the browser runner cannot resolve. It lives in the node
          // project below instead.
          exclude: ['**/node_modules/**', '**/*.rule.test.ts'],
          browser: {
            enabled: true,
            provider: playwright(),
            instances: [{ browser: 'chromium' }],
          },
        },
      },
      {
        extends: true,
        test: {
          name: 'node',
          environment: 'node',
          include: ['src/**/*.rule.test.ts'],
        },
      },
    ],
  },
})
