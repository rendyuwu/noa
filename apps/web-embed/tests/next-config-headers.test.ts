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

/**
 * The origin a sizing message is posted to is the origin the header names.
 *
 * The embed surfaces ask their host to resize the frame (`src/components/frame-sizer.tsx`) and a
 * `postMessage` needs a target origin. That target is only useful if it is the origin actually
 * framing the document — and the header is baked at `next build` while the target is read per
 * request, so the two are two reads of one variable and can disagree. Compared against each other
 * rather than each against a literal, which is the only comparison a drift would fail.
 */
describe('the framing header and the sizing message name one origin', () => {
  async function targetOrigin(): Promise<string | null> {
    const { resolveFrameTargetOrigin } = await import('../src/lib/embed/frame-origin')
    return resolveFrameTargetOrigin(process.env)
  }

  it('agree when the variable is the same at build and at request time', async () => {
    process.env[ENV_VAR] = 'http://chat.noa.internal:3080'

    const [entry] = await loadHeaders()

    expect(entry!.headers[0]!.value).toBe(`frame-ancestors ${await targetOrigin()}`)
  })

  it('disagree when the variable is set at build and gone at request time', async () => {
    // Recorded rather than merely allowed, because this is a *silent* failure: the CSP is the baked
    // value so the frame renders, the target is the pinned default so every message is dropped by
    // the browser for an origin mismatch, and there is no exception and no console error. What an
    // operator sees is a frame that never grows — indistinguishable from a host that stopped
    // listening. A deployment that moves LibreChat rebuilds; this is what happens if it does not.
    process.env[ENV_VAR] = 'http://chat.noa.internal:3080'
    const [entry] = await loadHeaders()

    delete process.env[ENV_VAR]

    expect(entry!.headers[0]!.value).not.toBe(`frame-ancestors ${await targetOrigin()}`)
  })

  it('never name a wildcard, on either side', async () => {
    process.env[ENV_VAR] = 'https://chat.noa.internal'

    const [entry] = await loadHeaders()

    expect(entry!.headers[0]!.value).not.toContain('*')
    expect(await targetOrigin()).not.toBe('*')
  })
})
