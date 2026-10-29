import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const apiProxyTarget = process.env.INFOOS_VITE_API_TARGET || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": apiProxyTarget
    }
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test-setup.ts"
  }
});
