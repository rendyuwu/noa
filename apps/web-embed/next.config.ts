import { fileURLToPath } from 'node:url'
import type { NextConfig } from 'next'

// This package is a standalone deploy artifact (C12). It is not part of a pnpm
// workspace and shares nothing with `apps/admin-web`, so both the Turbopack root
// and the output file trace stop here — otherwise a lockfile or `node_modules`
// higher up the monorepo drags unrelated files into the watch set and the
// standalone bundle.
const projectRoot = fileURLToPath(new URL('.', import.meta.url))

const nextConfig: NextConfig = {
  output: 'standalone',
  outputFileTracingRoot: projectRoot,
  turbopack: {
    root: projectRoot,
  },
}

export default nextConfig
