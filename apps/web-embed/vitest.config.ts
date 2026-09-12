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
    // The zone every spec runs under, and it has to be set here rather than in a
    // spec. `lib/format/jakarta-time.ts` builds its `Intl.DateTimeFormat` at
    // import time, so the zone that formatter captures is whatever the process
    // held before the test module graph evaluated. ESM hoists imports above any
    // `process.env.TZ = ...` line in a spec, and `setupFiles` runs after the
    // environment is up but the same problem applies — both are too late.
    // Vitest applies `test.env` when it spawns the worker, which is before any
    // of it. A `TZ=` prefix on the test script would also work but is invisible
    // to an IDE runner. New York because a "renders Jakarta time" assertion that
    // runs on a machine already in Jakarta is green against the exact bug it
    // exists to catch.
    env: { TZ: 'America/New_York' },
    // `e2e/` belongs to Playwright, which has its own runner and its own
    // expect. Vitest's default include would not match `*.e2e.ts`, but saying so
    // means a rename cannot quietly hand those specs to the wrong runner.
    exclude: ['e2e/**', 'node_modules/**', '.next/**'],
  },
})
