import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

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
      input: {
        accountInvitations: fileURLToPath(new URL("./src/entrypoints/accountInvitations.ts", import.meta.url)),
        membershipRequests: fileURLToPath(new URL("./src/entrypoints/membershipRequests.ts", import.meta.url)),
        membershipAuditLog: fileURLToPath(new URL("./src/entrypoints/membershipAuditLog.ts", import.meta.url)),
        membershipRequestDetail: fileURLToPath(new URL("./src/entrypoints/membershipRequestDetail.ts", import.meta.url)),
        membershipProfileNotes: fileURLToPath(new URL("./src/entrypoints/membershipProfileNotes.ts", import.meta.url)),
        membershipStats: fileURLToPath(new URL("./src/entrypoints/membershipStats.ts", import.meta.url)),
      },
    },
  },
}));