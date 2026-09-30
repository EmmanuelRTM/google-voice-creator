import { defineConfig } from "vite";

// The page calls POST /v1/live-tokens on its own origin. In development Vite forwards it
// to the Voice Creator server; in production, route it the same way on your reverse proxy.
export default defineConfig({
  server: {
    proxy: {
      "/v1/live-tokens": {
        target: process.env.VOICE_CREATOR_URL ?? "http://127.0.0.1:8765",
        changeOrigin: true,
      },
    },
  },
});
