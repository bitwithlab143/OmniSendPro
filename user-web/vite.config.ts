import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development the API is proxied so auth cookies stay same-origin (SameSite=Strict).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: { "/api": { target: process.env.API_URL ?? "http://localhost:8000", changeOrigin: false } },
  },
  preview: { port: 5174 },
  build: { sourcemap: false, chunkSizeWarningLimit: 700 },
  test: { environment: "jsdom", globals: true },
});
