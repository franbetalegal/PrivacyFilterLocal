import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `base: "./"` makes built asset paths relative so the bundle works when
// served by FastAPI from the application root. In dev, `/api` is proxied to
// the uvicorn backend on port 7860.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:7860",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
