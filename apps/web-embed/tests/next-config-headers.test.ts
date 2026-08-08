// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The framing header is actually wired into the config Next reads (§T.45, V41).
 *
 * `config/framing.test.ts` proves the rule; this proves the app applies it. A resolver nobody
 * calls holds nothing, and `headers()` is the one knob Next reads for this — the same reason §T.40
 * asserts `runtime`/`dynamic` on the route module's own exports rather than on a response.
 *
 * The variable is set before the import so the result does not depend on a developer's repo-root
 * `.env`: `loadRootEnv` fills only keys the environment does not already have (§T.44).
 *
 * Node environment, not the package default: `next.config.ts` resolves its own directory from
 * `import.meta.url`, and under jsdom that is an `http://` URL, so `fileURLToPath` throws before the
 * config is ever built. The subject is a Node-side config file, so it is read in a Node runtime.
 */

const ENV_VAR = 'NOA_LIBRECHAT_ORIGIN'

let original: string | undefined

async function loadHeaders() {
  vi.resetModules()
  const { default: config } = await import('../next.config')
  return await config.headers!()
}

beforeEach(() => {
  original = process.env[ENV_VAR]
})

afterEach(() => {
  if (original === undefined) delete process.env[ENV_VAR]
  else process.env[ENV_VAR] = original
  vi.resetModules()
})

describe('§T.45 — next.config.ts sends the framing header', () => {
  it('returns the frame-ancestors entry for the configured origin', async () => {
    process.env[ENV_VAR] = 'https://chat.noa.internal'

    expect(await loadHeaders()).toEqual([
      {
        source: '/(.*)',
        headers: [
          { key: 'Content-Security-Policy', value: 'frame-ancestors https://chat.noa.internal' },
        ],
      },
    ])
  })

  it('carries a different configured origin through — the separating case', async () => {
    // Without this, the assertion above passes just as well against a hardcoded string, and the
    // e2e lane (which points the dev server at its own origin) would have nothing behind it.
    process.env[ENV_VAR] = 'http://chat.noa.internal:3080'

    const [entry] = await loadHeaders()

    expect(entry!.headers[0]!.value).toBe('frame-ancestors http://chat.noa.internal:3080')
  })

  it('refuses to build with an origin that would widen the allowlist', async () => {
    process.env[ENV_VAR] = '*'

    await expect(loadHeaders()).rejects.toThrow(ENV_VAR)
  })
})
