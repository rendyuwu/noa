import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vitest/config'

const dirname = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  resolve: {
    alias: {
      '@': path.join(dirname, 'src'),
    },
  },
  test: {
    // Nearly every test here renders DOM (BIGSU pages, drawers, forms). Route
    // handlers keep working under jsdom because Vitest leaves Node's own globals
    // (Request, Response) in place for names jsdom does not define.
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    // `tests/**/*.server.test.ts` belongs to `vitest.server.config.ts` (`pnpm test:server`): those
    // specs boot a dev server, which is minutes of runtime this lane should not own. Named here so
    // a rename cannot quietly hand one to the wrong runner.
    exclude: ['node_modules/**', '.next/**', 'tests/**/*.server.test.ts'],
  },
})
