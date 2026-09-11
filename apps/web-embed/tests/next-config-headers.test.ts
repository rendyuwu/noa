// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The framing header is actually wired into the config Next reads.
 *
 * `config/framing.test.ts` proves the rule; this proves the app applies it. A resolver nobody
 * calls holds nothing, and `headers()` is the one knob Next reads for this — the same reason the
 * route config test asserts `runtime`/`dynamic` on the route module's own exports rather than on
 * a response.
 *
 * The variable is set before the import so the result does not depend on a developer's repo-root
 * `.env`: `loadRootEnv` fills only keys the environment does not already have.
 *
 * Node environment, not the package default: `next.config.ts` resolves its own directory from
 * `import.meta.url`, and under jsdom that is an `http://` URL, so `fileURLToPath` throws before the
 * config is ever built. The subject is a Node-side config file, so it is read in a Node runtime.
 */

const ENV_VAR = 'NOA_LIBRECHAT_ORIGIN'
const PUBLIC_ENV_VAR = 'NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN'

let original: Record<string, string | undefined> = {}

async function loadHeaders() {
  vi.resetModules()
  const { default: config } = await import('../next.config')
  return await config.headers!()
}

function set(name: string, value: string | undefined): void {
  if (value === undefined) delete process.env[name]
  else process.env[name] = value
}

beforeEach(() => {
  original = { [ENV_VAR]: process.env[ENV_VAR], [PUBLIC_ENV_VAR]: process.env[PUBLIC_ENV_VAR] }
})

afterEach(() => {
  for (const [name, value] of Object.entries(original)) set(name, value)
  vi.resetModules()
})

describe('next.config.ts sends the framing header', () => {
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
 * framing the document. Both values are baked at `next build` — the header out of this config, the
 * target out of the `NEXT_PUBLIC_*` twin Next inlines — so this is the one file where both halves
 * are in scope at once and the only place the pair can be compared against each other rather than
 * each against a literal, which is the only comparison a drift would fail.
 *
 * **Two variables, and that is the fix rather than the hazard.** A single name would have to serve
 * both, and the header must not read a `NEXT_PUBLIC_*` one: a build or compose file that set only
 * the private name — every one written before the twin existed — would then emit the development
 * default while reading as configured, silently. Splitting the names moves the remaining
 * half-configured case somewhere an operator can see it, which is what the third spec below is.
 */
describe('the framing header and the sizing message name one origin', () => {
  async function targetOrigin(): Promise<string | null> {
    const { resolveFrameTargetOrigin } = await import('../src/lib/embed/frame-origin')
    return resolveFrameTargetOrigin()
  }

  it('agree when both names carry one origin', async () => {
    set(ENV_VAR, 'http://chat.noa.internal:3080')
    set(PUBLIC_ENV_VAR, 'http://chat.noa.internal:3080')

    const [entry] = await loadHeaders()

    expect(entry!.headers[0]!.value).toBe(`frame-ancestors ${await targetOrigin()}`)
  })

  it('the message target follows the public name, not the header one', async () => {
    // The separating case, and it is the whole reason the split is safe to make. Wired to the
    // private name the resolver would pass every agreement spec here and still be the variable
    // Next cannot inline, so the shipped build would read `undefined` and post nothing.
    set(ENV_VAR, 'http://chat.noa.internal:3080')
    set(PUBLIC_ENV_VAR, 'http://chat.a3probe.test:9443')

    expect(await targetOrigin()).toBe('http://chat.a3probe.test:9443')
  })

  it('a header-only configuration says so on the page instead of naming a default', async () => {
    // This replaces a spec that recorded the opposite behaviour as intended. It used to be true
    // that an origin present for the header and absent for the target left the target on the pinned
    // development default: the frame rendered, every message was dropped by the browser for an
    // origin mismatch, and there was no exception and no console error — a frame that never grows,
    // indistinguishable from a host that stopped listening. `null` is what `FrameSizer` turns into
    // `data-noa-frame-size="no-target-origin"`, in the DOM, findable by anyone looking at the card.
    set(ENV_VAR, 'http://chat.noa.internal:3080')
    set(PUBLIC_ENV_VAR, undefined)

    const [entry] = await loadHeaders()

    expect(entry!.headers[0]!.value).toBe('frame-ancestors http://chat.noa.internal:3080')
    expect(await targetOrigin()).toBeNull()
  })

  it('never name a wildcard, on either side', async () => {
    set(ENV_VAR, 'https://chat.noa.internal')
    set(PUBLIC_ENV_VAR, 'https://chat.noa.internal')

    const [entry] = await loadHeaders()

    expect(entry!.headers[0]!.value).not.toContain('*')
    expect(await targetOrigin()).not.toBe('*')
  })
})
