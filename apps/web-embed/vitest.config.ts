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
    // Every test this package will grow renders DOM. Route handlers keep working
    // under jsdom because Vitest leaves Node's own globals (Request, Response)
    // in place for names jsdom does not define.
    environment: 'jsdom',
    globals: true,
    // `e2e/` belongs to Playwright, which has its own runner and its own
    // expect. Vitest's default include would not match `*.e2e.ts`, but saying so
    // means a rename cannot quietly hand those specs to the wrong runner.
    exclude: ['e2e/**', 'node_modules/**', '.next/**'],
  },
})
