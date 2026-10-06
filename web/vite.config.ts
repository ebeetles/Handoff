import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, fs: { allow: [".."] }, // allow importing ../contracts fixtures in tests
    proxy: { "/api/composer": { target: "http://127.0.0.1:8000", timeout: 480000, proxyTimeout: 480000 } } },
  // signalsmith-stretch builds its AudioWorklet from its own functions' source text; Vite's
  // dependency pre-bundling rewrites them and the worklet never starts. Serve it as published.
  optimizeDeps: { exclude: ["signalsmith-stretch"] },
  test: { environment: "node" },
} as never);
