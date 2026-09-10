import { fileURLToPath } from 'node:url'
import type { NextConfig } from 'next'

import { buildFramingHeaders } from './config/framing'
import { loadRootEnv } from './config/root-env'

// This package is a standalone deploy artifact. It is not part of a pnpm
// workspace and shares nothing with `apps/admin-web`, so both the Turbopack root
// and the output file trace stop here — otherwise a lockfile or `node_modules`
// higher up the monorepo drags unrelated files into the watch set and the
// standalone bundle.
const projectRoot = fileURLToPath(new URL('.', import.meta.url))

// The repo-root `.env` is the single source for local dev (the embed session plumbing's loading;
// no secrets in git). Anything
// already in the real environment wins — see `config/root-env.ts`.
loadRootEnv(projectRoot, process.env)

const nextConfig: NextConfig = {
  output: 'standalone',
  outputFileTracingRoot: projectRoot,
  turbopack: {
    root: projectRoot,
  },
  // Framing allowlist (the embed's frame-ancestors CSP; one frame-ancestors entry on every
  // response). Read after `loadRootEnv` above, so the repo-root `.env` has
  // already been applied. Note that `output: 'standalone'` never executes this config at runtime:
  // the origin is baked at `next build`, which means a runtime variable cannot widen the allowlist
  // — and cannot change it either, so a deployment that moves LibreChat rebuilds (fixed at the
  // per-app Dockerfile and domain setup).
  headers: async () => buildFramingHeaders(process.env),
}

export default nextConfig
