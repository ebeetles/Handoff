import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, fs: { allow: [".."] }, // allow importing ../contracts fixtures in tests
    proxy: { "/api/composer": { target: "http://127.0.0.1:8000", timeout: 240000, proxyTimeout: 240000 } } },
  test: { environment: "node" },
} as never);
