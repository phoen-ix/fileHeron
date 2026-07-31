import path from 'node:path'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    watch: { usePolling: true },
    proxy: {
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true,
      },
      // /uploads/* is the TUS protocol surface - streams to tusd.
      // Browser → Vite → tusd. No buffering on either hop.
      // tusd listens on its default port 8080.
      //
      // `changeOrigin` rewrites Host to `tusd:8080`, and tusd (-behind-proxy)
      // builds the upload Location from the Host header - so every dev-stack
      // upload got a Location of http://tusd:8080/uploads/<id>, an address the
      // browser cannot resolve, and the first PATCH failed. Forwarding the
      // ORIGINAL host is what makes tusd emit a URL the browser can use; the
      // production edge does the same thing with `proxy_set_header Host
      // $http_host` (audit 2026-07-30, nginx-11).
      '/uploads': {
        target: 'http://tusd:8080',
        changeOrigin: true,
        headers: {},
        configure: (proxy) => {
          proxy.on('proxyReq', (proxyReq, req) => {
            const original = req.headers.host
            if (original) proxyReq.setHeader('X-Forwarded-Host', original)
          })
        },
      },
    },
  },
})
