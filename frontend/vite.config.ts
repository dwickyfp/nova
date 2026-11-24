/// <reference types="vitest/config" />
import { existsSync, readdirSync, readFileSync, writeFileSync } from "fs";
import path from "path";
import { defineConfig, transformWithOxc, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import { playwright } from "@vitest/browser-playwright";

const THIRD_PARTY = "licenses/third-party.md";

/** Append the NOTICE file of every bundled package that ships one (Apache-2.0 section 4d). */
function thirdPartyNotices(): Plugin {
  return {
    name: "third-party-notices",
    apply: "build",
    writeBundle(options) {
      const file = path.join(options.dir ?? "dist", THIRD_PARTY);
      if (!existsSync(file)) return;
      const listing = readFileSync(file, "utf8");
      const store = path.resolve(import.meta.dirname, "node_modules/.pnpm");
      const notices: string[] = [];
      for (const [, name, version] of listing.matchAll(/^## (\S+) - (\S+) \(/gm)) {
        const prefix = `${name.replace("/", "+")}@${version}`;
        for (const entry of readdirSync(store).filter((item) => item === prefix || item.startsWith(prefix + "_"))) {
          const directory = path.join(store, entry, "node_modules", name);
          const notice = existsSync(directory) && readdirSync(directory).find((item) => /^NOTICE(\.(md|txt))?$/i.test(item));
          if (notice) {
            notices.push(`### ${name} - ${version}\n\n${readFileSync(path.join(directory, notice), "utf8").trim()}\n`);
            break;
          }
        }
      }
      if (notices.length) writeFileSync(file, `${listing.trimEnd()}\n\n## NOTICE files\n\n${notices.join("\n")}`);
    },
  };
}

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
    thirdPartyNotices(),
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
    // Every bundled dependency's license, served beside the hand-kept map notices.
    license: { fileName: THIRD_PARTY },
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
      // Pre-bundled so a cold test run does not re-optimize mid-run and load React twice.
      "@radix-ui/react-context-menu", "@radix-ui/react-collapsible",
    ],
  },
  server: {
    watch: { ignored: ["**/coverage/**", "**/__screenshots__/**"] },
    proxy: {
      "^/api/v1/viz/datasets/(inspect|[0-9a-fA-F-]{36}/(preview|query))/?(\\?|$)": {
        target: process.env.NOVA_QUERY_API_URL ?? process.env.NOVA_WEB_API_URL ?? "http://localhost:8000",
        changeOrigin: true,
      },
      // Query and ML use their workload grants. Studio and assistant metadata
      // remain owned by web until the dedicated Agent runtime is delivered.
      "^/api/v1/(query|ml)(/|$)": {
        target: process.env.NOVA_QUERY_API_URL ?? process.env.NOVA_WEB_API_URL ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/api": {
        target: process.env.NOVA_WEB_API_URL ?? "http://localhost:8000",
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
          exclude: ["**/node_modules/**", "**/*.rule.test.ts", "**/*.perf.test.tsx"],
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
          // Frame-time budgets are timed by the wall clock. Other test files
          // running alongside take the processor away and slow every frame,
          // so these run on their own, one file at a time: `pnpm test:perf`.
          name: "perf",
          include: ["src/**/*.perf.test.tsx"],
          fileParallelism: false,
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
          include: ["src/**/*.rule.test.ts", "tests/**/*.rule.test.ts"],
        },
      },
    ],
  },
}));
