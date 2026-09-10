import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * Dependency-pin guards.
 *
 * Exact pins put the web stack on the same footing as Python's window: the versions are
 * a decision, and a bump has to be deliberate. `next`, `react` and `react-dom`
 * are named there explicitly because a bump on any of them re-opens the
 * LibreChat render gate (re-verified on every LibreChat bump) — the sandbox string and
 * the render mode that let the approval card work were measured against a pinned pair, not
 * inferred.
 *
 * Mirrors `apps/api/tests/test_pins.py::test_all_dependencies_exactly_pinned`.
 */

const APP_ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

// Anything that is not a bare version. `^`/`~` float, comparators float, `*`
// and `x` float, `||` offers a choice, and a `git:`/`file:` specifier is not a
// version at all.
const NOT_EXACT = /[\^~><=*|]|\bx\b|^git|^file:|^link:|^workspace:/

type PackageJson = {
  dependencies: Record<string, string>
  devDependencies: Record<string, string>
  engines: Record<string, string>
  packageManager: string
}

function packageJson(): PackageJson {
  return JSON.parse(readFileSync(path.join(APP_ROOT, 'package.json'), 'utf8')) as PackageJson
}

describe('Exact pins — web dependencies are pinned exactly', () => {
  it.each(['next', 'react', 'react-dom'])(
    'pins %s to a bare version, never a range',
    (name: string) => {
      const spec = packageJson().dependencies[name]

      expect(spec, `${name} is absent from dependencies`).toBeDefined()
      expect(spec).not.toMatch(NOT_EXACT)
    },
  )

  it('pins next to 16 and react to 19 (the exact-pins rule names the majors)', () => {
    const deps = packageJson().dependencies

    expect(deps['next']).toMatch(/^16\./)
    expect(deps['react']).toMatch(/^19\./)
    expect(deps['react-dom']).toBe(deps['react'])
  })

  it('leaves no range specifier anywhere, dev dependencies included', () => {
    const { dependencies, devDependencies } = packageJson()
    const floating = Object.entries({ ...dependencies, ...devDependencies })
      .filter(([, spec]) => NOT_EXACT.test(spec))
      .map(([name, spec]) => `${name}@${spec}`)

    expect(floating).toEqual([])
  })

  it('declares the Node floor the exact-pins rule sets', () => {
    // The exact-pins rule says Node 20+. Stated on the package so `pnpm install` refuses an older
    // runtime, rather than left to the README to remember.
    expect(packageJson().engines['node']).toBe('>=20.9.0')
  })

  it('declares pnpm as the package manager', () => {
    expect(packageJson().packageManager).toMatch(/^pnpm@/)
  })

  it('keeps @types/node on the runtime major it types', () => {
    // A types package a major ahead of the runtime describes APIs that are not
    // there, and `tsc` then accepts code the deploy cannot run. Node's floor is
    // 20 and the toolchain here runs 24.
    expect(packageJson().devDependencies['@types/node']).toMatch(/^24\./)
  })
})
