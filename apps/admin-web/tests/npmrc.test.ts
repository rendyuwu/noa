import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * Registry routing for the BIGSU scope — the admin scaffold's internal registry `.npmrc`.
 *
 * `@gio/*` does not exist on the public registry. Without this mapping every
 * BIGSU install resolves to a 404 — or, worse, to whatever a future squatter
 * publishes under the name. The file is one line, which is exactly why it is the
 * kind of thing a `pnpm install --force` or a Dockerfile that copies only
 * `package.json` drops without anyone noticing.
 */

const APP_ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const REGISTRY = 'https://bigsu.biznetgio.pt/registry/'

function npmrc(): string {
  return readFileSync(path.join(APP_ROOT, '.npmrc'), 'utf8')
}

function packageNames(): string[] {
  const pkg = JSON.parse(readFileSync(path.join(APP_ROOT, 'package.json'), 'utf8')) as {
    dependencies: Record<string, string>
    devDependencies: Record<string, string>
  }

  return Object.keys({ ...pkg.dependencies, ...pkg.devDependencies })
}

describe('the @gio scope resolves from the Biznet Gio registry', () => {
  it('maps the scope to the internal registry', () => {
    expect(npmrc()).toContain(`@gio:registry=${REGISTRY}`)
  })

  it('scopes the mapping — a bare `registry=` would send every package there', () => {
    // A global default would route the public dependencies through an internal
    // mirror too: a different supply chain than the lockfile was resolved
    // against, and one outage away from a build that cannot install React.
    const globalDefault = npmrc()
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.startsWith('registry='))

    expect(globalDefault).toEqual([])
  })

  it('commits no credential — the network is the access boundary', () => {
    // No secrets in git. The internal registry is reachable without a
    // token from inside the network, so an `_auth`/`_authToken` line here would
    // be both unnecessary and a leak.
    expect(npmrc()).not.toMatch(/_auth|_password|authToken/i)
  })

  it('scans a manifest that actually asks for @gio packages', () => {
    // Without this the mapping above is a rule about nothing: a package.json
    // that lost its BIGSU dependencies would still carry a correct `.npmrc` and
    // read as green.
    expect(packageNames().filter((name) => name.startsWith('@gio/')).length).toBeGreaterThan(0)
  })
})
