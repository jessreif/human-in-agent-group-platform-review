/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

import { BASE_PATH } from "./src/config";

// Participant UI is served under /investigators/ (spec §3.1). Apache strips that prefix before
// proxying to the UI container, whose nginx serves the build at "/".
const sourceSha = process.env.INVESTIGATORS_BUILD_SOURCE_SHA256 || "unset";
const buildTime = process.env.INVESTIGATORS_BUILD_TIME || "unset";

// Emits dist/version.json so the UI build carries the same stamp as the API (INV-0105).
function versionStamp(): Plugin {
  return {
    name: "investigators-version-stamp",
    apply: "build",
    generateBundle() {
      this.emitFile({
        type: "asset",
        fileName: "version.json",
        source: JSON.stringify(
          { service: "investigators-ui", source_sha256: sourceSha, build_time: buildTime },
          null,
          2,
        ),
      });
    },
  };
}

// Opt-in for the participant browser scenarios. Unset (the default, including
// scripts/check.sh) leaves Vite with no proxy, so a dev server cannot reach an API.
const e2eApiPort = process.env.INVESTIGATORS_E2E_API_PORT;
if (e2eApiPort !== undefined && !/^[0-9]+$/.test(e2eApiPort)) {
  throw new Error("INVESTIGATORS_E2E_API_PORT must be a port number");
}

// Shared by `vite` and `vite preview`. The browser's Host is the Vite origin.
// The API compares Origin to Host on the event stream, so the proxy must not
// rewrite Host. Vite's own error handler leaves a response alone once headers
// are sent, which is exactly the event stream — destroy it so a killed upstream
// socket reaches the browser.
const e2eProxy = e2eApiPort
  ? {
      "/investigators/api": {
        target: `http://127.0.0.1:${e2eApiPort}`,
        changeOrigin: false,
        rewrite: (path: string) => path.replace(/^\/investigators\/api/, "") || "/",
        configure: (proxy: {
          on: (event: "error", handler: (err: unknown, req: unknown, res: unknown) => void) => void;
        }) => {
          proxy.on("error", (_err, _req, res) => {
            if (res === null || typeof res !== "object") return;
            const response = res as {
              headersSent?: boolean;
              writableEnded?: boolean;
              destroy?: () => void;
            };
            if (response.headersSent && !response.writableEnded) response.destroy?.();
          });
        },
      },
    }
  : null;

export default defineConfig({
  base: BASE_PATH,
  plugins: [react(), versionStamp()],
  ...(e2eProxy ? { server: { proxy: e2eProxy }, preview: { proxy: e2eProxy } } : {}),
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    assetsDir: "assets",
  },
  test: {
    // jsdom stays the default. Vitest 4 dropped environmentMatchGlobs; these
    // projects are the opt-in. Only the DOM-free clock and store files use Node.
    environment: "jsdom",
    projects: [
      {
        extends: true,
        test: {
          name: "node",
          environment: "node",
          include: ["tests/clock.test.ts", "tests/store.test.ts"],
        },
      },
      {
        extends: true,
        test: {
          name: "jsdom",
          environment: "jsdom",
          include: ["tests/**/*.test.{ts,tsx}"],
          exclude: [
            "**/node_modules/**",
            "**/.git/**",
            "tests/clock.test.ts",
            "tests/store.test.ts",
          ],
        },
      },
    ],
  },
});
