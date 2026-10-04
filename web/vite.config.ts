import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, fs: { allow: [".."] } }, // allow importing ../contracts fixtures in tests
  test: { environment: "node" },
} as never);
