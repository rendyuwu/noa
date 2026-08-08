import { fileURLToPath } from 'node:url'
import type { NextConfig } from 'next'

import { loadRootEnv } from './config/root-env'

// This package is a standalone deploy artifact (C12). It is not part of a pnpm
// workspace and shares nothing with `apps/admin-web`, so both the Turbopack root
// and the output file trace stop here — otherwise a lockfile or `node_modules`
// higher up the monorepo drags unrelated files into the watch set and the
// standalone bundle.
const projectRoot = fileURLToPath(new URL('.', import.meta.url))

// The repo-root `.env` is the single source for local dev (§T.44, C11). Anything
// already in the real environment wins — see `config/root-env.ts`.
loadRootEnv(projectRoot, process.env)

const nextConfig: NextConfig = {
  output: 'standalone',
  outputFileTracingRoot: projectRoot,
  turbopack: {
    root: projectRoot,
  },
}

export default nextConfig
