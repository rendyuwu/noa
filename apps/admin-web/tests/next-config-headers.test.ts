// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * The framing header is actually wired into the config Next reads (§T.49, V41).
 *
 * `config/framing.test.ts` proves the rule; this proves the app applies it. A rule nobody calls
 * holds nothing, and `headers()` is the one knob Next reads for this.
 *
 * Node environment, not the package default: `next.config.ts` resolves its own directory from
 * `import.meta.url`, and under jsdom that is an `http://` URL, so `fileURLToPath` throws before the
 * config is ever built. The subject is a Node-side config file, so it is read in a Node runtime.
 */

// The embed's variable (§T.45). It lives in the same `.env` and on the same host, and it must mean
// nothing here.
const EMBED_ENV_VAR = 'NOA_LIBRECHAT_ORIGIN'

let original: string | undefined

async function loadHeaders() {
  vi.resetModules()
  const { default: config } = await import('../next.config')
  return await config.headers!()
}

beforeEach(() => {
  original = process.env[EMBED_ENV_VAR]
})

afterEach(() => {
  if (original === undefined) delete process.env[EMBED_ENV_VAR]
  else process.env[EMBED_ENV_VAR] = original
  vi.resetModules()
})

describe('§T.49 — next.config.ts sends the framing header', () => {
  it("returns the frame-ancestors 'none' entry", async () => {
    expect(await loadHeaders()).toEqual([
      {
        source: '/(.*)',
        headers: [{ key: 'Content-Security-Policy', value: "frame-ancestors 'none'" }],
      },
    ])
  })

  it("stays 'none' with the embed's origin variable set — the separating case", async () => {
    // The repo-root `.env` is shared by both web apps (§T.44, §T.47), so this variable is present
    // in the environment this config is built in. V41 gives the embed an allowlist and this app
    // none; a value that leaked across would frame the admin panel from the chat origin.
    process.env[EMBED_ENV_VAR] = 'https://chat.noa.internal'

    const [entry] = await loadHeaders()

    expect(entry!.headers[0]!.value).toBe("frame-ancestors 'none'")
  })
})
