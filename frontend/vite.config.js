import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Proxying /api and /ws to the backend means the browser only ever talks to
// one origin. No CORS, no hardcoded localhost:8000 in the source, and the
// production build works unchanged behind any reverse proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks: {
          // Three.js engine — large but cached after first load
          "three": ["three"],
          // React Three Fiber + Drei helpers
          "r3f": ["@react-three/fiber", "@react-three/drei"],
          // React core
          "react-vendor": ["react", "react-dom"],
        },
      },
    },
  },
});

