import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

import { buildEntrypointInputs } from "./src/config/viteEntrypoints";

export default defineConfig(({ command }) => ({
  appType: "custom",
  base: command === "build" ? "/static/bundler/" : "/",
  plugins: [vue()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    watch: {
      usePolling: true,
    },
  },
  build: {
    manifest: "manifest.json",
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: buildEntrypointInputs(new URL("./", import.meta.url)),
    },
  },
}));