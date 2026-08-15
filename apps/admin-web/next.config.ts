import { fileURLToPath } from 'node:url'
import type { NextConfig } from 'next'

import { buildFramingHeaders } from './config/framing'
import { loadRootEnv } from './config/root-env'

// This package is a standalone deploy artifact (C12). It is not part of a pnpm
// workspace and shares nothing with `apps/web-embed`, so both the Turbopack root
// and the output file trace stop here — otherwise a lockfile or `node_modules`
// higher up the monorepo drags unrelated files into the watch set and the
// standalone bundle.
const projectRoot = fileURLToPath(new URL('.', import.meta.url))

// The repo-root `.env` is the single source for local dev (§T.47, C11). Anything
// already in the real environment wins — see `config/root-env.ts`.
loadRootEnv(projectRoot, process.env)

const nextConfig: NextConfig = {
  output: 'standalone',
  outputFileTracingRoot: projectRoot,
  turbopack: {
    root: projectRoot,
  },
  // Nobody may frame this app (§T.49, V41). One entry on `/(.*)`, so the pages, `/healthz`, the 404
  // and the `/api/*` proxy §T.50 will add are covered without each route remembering a guard for
  // itself. Unlike the embed's copy (§T.45), nothing here is read from the environment: there is no
  // legitimate parent to name, so no variable can widen this at build time or at runtime.
  headers: async () => buildFramingHeaders(),
}

export default nextConfig
