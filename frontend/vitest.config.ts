import { defineConfig, mergeConfig } from 'vitest/config'

import viteConfig from './vite.config.ts'

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      globals: true,
      environment: 'happy-dom',
      include: ['tests/**/*.test.ts', 'src/**/*.test.ts'],
      // Fail any test that makes a real HTTP request, or that leaves a Vue
      // warning behind (see the files).
      setupFiles: ['tests/setup/noRealNetwork.ts', 'tests/setup/noVueWarnings.ts'],
    },
  }),
)
