import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Phát triển: frontend :5173, API :8100 (proxy cả WebSocket).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    fs: { allow: [".."] },
    proxy: {
      "/api": "http://127.0.0.1:8100",
      "/ws": { target: "ws://127.0.0.1:8100", ws: true },
    },
  },
  build: { chunkSizeWarningLimit: 1500 },
});
