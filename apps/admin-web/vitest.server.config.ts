import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vitest/config'

const dirname = path.dirname(fileURLToPath(import.meta.url))

/**
 * The lane that boots a real server (`pnpm test:server`).
 *
 * Separate from `vitest.config.ts` because these specs spawn `next dev` and wait on it: minutes,
 * not milliseconds, and a compiler holding a port is not something the unit lane should own. The
 * unit config excludes this glob, so a spec is in exactly one runner.
 */
export default defineConfig({
  resolve: {
    alias: {
      '@': path.join(dirname, 'src'),
    },
  },
  test: {
    environment: 'node',
    globals: true,
    include: ['tests/**/*.server.test.ts'],
    // One server per file, and the boot is the expensive part. Running files in parallel would
    // start several dev compilers over the same `.next` directory.
    fileParallelism: false,
    testTimeout: 180_000,
    hookTimeout: 180_000,
  },
})
