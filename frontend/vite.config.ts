import react from "@vitejs/plugin-react";
import { defineConfig, mergeConfig } from "vite";
// vitest bundles its own nested copy of `vite` (a real, confirmed npm
// hoisting artifact in this install), so its `defineConfig`'s Plugin
// types don't structurally match plain "vite"'s -- mergeConfig() is
// Vitest's own documented workaround: build the Vite config and the
// vitest `test` config as two separately-typed objects, then merge them
// at runtime rather than trying to satisfy one combined type.
import { defineConfig as defineVitestConfig } from "vitest/config";

// Proxy /api during `npm run dev` so the dev server can run standalone
// against a locally-running backend (uvicorn on :8080, or the deployed
// container on :8099) without CORS wiring in the FastAPI app itself --
// the production build is served BY that same FastAPI app instead (see
// Dockerfile's frontend-build stage + api.py's StaticFiles mount), where
// this proxy is irrelevant.
export default mergeConfig(
  defineConfig({
    plugins: [react()],
    server: {
      proxy: {
        "/api": "http://localhost:8099",
      },
    },
    build: {
      outDir: "dist",
    },
  }),
  defineVitestConfig({
    test: {
      environment: "jsdom",
      setupFiles: "./src/setupTests.ts",
      globals: true,
    },
  }),
);
