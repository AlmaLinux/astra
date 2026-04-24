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
        membershipSponsors: fileURLToPath(new URL("./src/entrypoints/membershipSponsors.ts", import.meta.url)),
        membershipRequestDetail: fileURLToPath(new URL("./src/entrypoints/membershipRequestDetail.ts", import.meta.url)),
        membershipProfileNotes: fileURLToPath(new URL("./src/entrypoints/membershipProfileNotes.ts", import.meta.url)),
        membershipStats: fileURLToPath(new URL("./src/entrypoints/membershipStats.ts", import.meta.url)),
        users: fileURLToPath(new URL("./src/entrypoints/users.ts", import.meta.url)),
        userProfile: fileURLToPath(new URL("./src/entrypoints/userProfile.ts", import.meta.url)),
        organizations: fileURLToPath(new URL("./src/entrypoints/organizations.ts", import.meta.url)),
        organizationDetail: fileURLToPath(new URL("./src/entrypoints/organizationDetail.ts", import.meta.url)),
        organizationForm: fileURLToPath(new URL("./src/entrypoints/organizationForm.ts", import.meta.url)),
        organizationClaim: fileURLToPath(new URL("./src/entrypoints/organizationClaim.ts", import.meta.url)),
      },
    },
  },
}));