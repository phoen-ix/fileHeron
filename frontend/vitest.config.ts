import { defineConfig, mergeConfig } from 'vitest/config'

import viteConfig from './vite.config'

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      globals: true,
      environment: 'happy-dom',
      include: ['tests/**/*.test.ts', 'src/**/*.test.ts'],
      // Fails any test that makes a real HTTP request (see the file).
      setupFiles: ['tests/setup/noRealNetwork.ts'],
    },
  }),
)
