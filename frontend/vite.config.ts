/// <reference types="vitest/config" />
import path from "path";
import { defineConfig, transformWithOxc } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import { playwright } from "@vitest/browser-playwright";

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [
    {
      name: "maplibre-class-fields",
      enforce: "pre",
      async transform(code, id) {
        if (id.includes("/maplibre-gl/src/") && id.endsWith(".ts")) {
          // MapLibre's declared typed views must retain values initialized by StructArray.
          return transformWithOxc(code, id, {
            typescript: { removeClassFieldsWithoutInitializer: true },
            assumptions: { setPublicClassFields: true },
          });
        }
      },
    },
    tanstackRouter({
      target: "react",
      autoCodeSplitting: true,
    }),
    react(),
    tailwindcss(),
  ],
  resolve: {
    dedupe: ["react", "react-dom"],
    alias: [
      // Split modular source for production. Development uses the compiled entry
      // because dependency prebundling bypasses the class-field transform above.
      {
        find: /^maplibre-gl$/,
        replacement: path.resolve(
          import.meta.dirname,
          command === "build"
            ? "./node_modules/maplibre-gl/src/index.ts"
            : "./node_modules/maplibre-gl/dist/maplibre-gl.mjs",
        ),
      },
      { find: "@", replacement: path.resolve(import.meta.dirname, "./src") },
    ],
  },
  build: {
    rolldownOptions: {
      input: { app: path.resolve(import.meta.dirname, 'index.html') },
      output: {
        strictExecutionOrder: true,
        codeSplitting: {
          maxSize: 400_000,
          groups: [{ name: "vendor", test: /node_modules/, maxSize: 400_000 }],
        },
      },
    },
  },
  optimizeDeps: {
    include: [
      "react", "react-dom/client", "react-top-loading-bar", "recharts",
      "@openuidev/lang-core", "@openuidev/react-lang", "@terraformer/wkt",
    ],
  },
  server: {
    watch: { ignored: ["**/coverage/**", "**/__screenshots__/**"] },
    proxy: {
      // SQL, ML and agent execution can run in a separate query process
      // (`./dev.sh --split`). Listed first because the first match wins; the
      // path boundary keeps /api/v1/query-autopilot on the web process.
      "^/api/v1/(query|ml|assistant|agents|intelligence/actions)(/|$)": {
        target: process.env.NOVA_QUERY_API_URL ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    silent: "passed-only",
    unstubEnvs: true,
    coverage: {
      // Report over all source so untested modules surface at 0% instead of
      // vanishing from the denominator. No `thresholds` yet: the floor is
      // report-only until the measured baseline is reviewed (see
      // docs/testing/STANDARD.md SS7).
      include: ["src/**/*.{js,jsx,ts,tsx}"],
      exclude: [
        "src/components/ui/**",
        "src/assets/**",
        "src/tanstack-table.d.ts",
        "src/routeTree.gen.ts",
        "src/test-utils/**",
        // Interim: route files are exercised by the L4 Playwright suite, which
        // does not exist yet. Remove this exclusion when it does (STANDARD SS3).
        "src/routes/**",
      ],
    },
    projects: [
      {
        extends: true,
        test: {
          name: "browser",
          // The design-system rule test loads ESLint, which reaches for Node
          // built-ins the browser runner cannot resolve. It lives in the node
          // project below instead.
          exclude: ["**/node_modules/**", "**/*.rule.test.ts"],
          browser: {
            enabled: true,
            provider: playwright(),
            instances: [{ browser: "chromium" }],
          },
        },
      },
      {
        extends: true,
        test: {
          name: "node",
          environment: "node",
          include: ["src/**/*.rule.test.ts"],
        },
      },
    ],
  },
}));
