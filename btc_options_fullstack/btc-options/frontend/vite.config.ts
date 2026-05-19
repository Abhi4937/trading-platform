import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 3000,
    proxy: {
      // M7 Friday-band D1 multi-tiebreaker builds can take 25-40s on cold
      // backend (per-trade archive load + in-process grid build). The
      // default proxy/connect timeouts kill these. Bump both to 5 min.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        timeout:      300_000,
        proxyTimeout: 300_000,
      },
      '/ws':  { target: 'ws://localhost:8000',   ws: true, changeOrigin: true },
    },
  },
  watch: {
    usePolling: true,
    interval: 1000,
  },
});
