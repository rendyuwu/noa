import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * Dependency-pin guards (C2, §T.47).
 *
 * C2 puts the web stack on the same footing as C1 puts Python: the versions are
 * a decision, and a bump has to be deliberate. `next`, `react` and `react-dom`
 * are named there explicitly, and BIGSU is pinned for the same reason — the
 * design system arrives from an internal registry whose `latest` tag can move
 * under a caret without anyone deciding anything.
 *
 * Mirrors `apps/web-embed/tests/pins.test.ts` and
 * `apps/api/tests/test_pins.py::test_all_dependencies_exactly_pinned`.
 */

const APP_ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

// Anything that is not a bare version. `^`/`~` float, comparators float, `*`
// and `x` float, `||` offers a choice, and a `git:`/`file:` specifier is not a
// version at all.
const NOT_EXACT = /[\^~><=*|]|\bx\b|^git|^file:|^link:|^workspace:/

const BIGSU_PACKAGES = [
  '@gio/bigsu-ui',
  '@gio/bigsu-icons',
  '@gio/bigsu-tokens',
  '@gio/bigsu-app-shell',
]

type PackageJson = {
  dependencies: Record<string, string>
  devDependencies: Record<string, string>
  engines: Record<string, string>
  packageManager: string
}

function packageJson(): PackageJson {
  return JSON.parse(readFileSync(path.join(APP_ROOT, 'package.json'), 'utf8')) as PackageJson
}

describe('C2 — web dependencies are pinned exactly', () => {
  it.each(['next', 'react', 'react-dom'])(
    'pins %s to a bare version, never a range',
    (name: string) => {
      const spec = packageJson().dependencies[name]

      expect(spec, `${name} is absent from dependencies`).toBeDefined()
      expect(spec).not.toMatch(NOT_EXACT)
    },
  )

  it('pins next to 16 and react to 19 (C2 names the majors)', () => {
    const deps = packageJson().dependencies

    expect(deps['next']).toMatch(/^16\./)
    expect(deps['react']).toMatch(/^19\./)
    expect(deps['react-dom']).toBe(deps['react'])
  })

  it.each(BIGSU_PACKAGES)('pins %s — §T.47 names BIGSU as a scaffold dependency', (name: string) => {
    const spec = packageJson().dependencies[name]

    expect(spec, `${name} is absent from dependencies`).toBeDefined()
    expect(spec).not.toMatch(NOT_EXACT)
  })

  it('leaves no range specifier anywhere, dev dependencies included', () => {
    const { dependencies, devDependencies } = packageJson()
    const floating = Object.entries({ ...dependencies, ...devDependencies })
      .filter(([, spec]) => NOT_EXACT.test(spec))
      .map(([name, spec]) => `${name}@${spec}`)

    expect(floating).toEqual([])
  })

  it('separates — the regex the suite above leans on still rejects a range', () => {
    // Without this the three assertions above pass on a broken regex, and a
    // caret would ship reading as a pin.
    expect(NOT_EXACT.test('^16.3.0')).toBe(true)
    expect(NOT_EXACT.test('~19.2.8')).toBe(true)
    expect(NOT_EXACT.test('16.3.0')).toBe(false)
  })

  it('declares the Node floor C2 sets', () => {
    // C2 says Node 20+. Stated on the package so `pnpm install` refuses an older
    // runtime, rather than left to the README to remember.
    expect(packageJson().engines['node']).toBe('>=20.9.0')
  })

  it('declares pnpm as the package manager (C2)', () => {
    expect(packageJson().packageManager).toMatch(/^pnpm@/)
  })

  it('keeps @types/node on the runtime major it types', () => {
    // A types package a major ahead of the runtime describes APIs that are not
    // there, and `tsc` then accepts code the deploy cannot run. Node's floor is
    // 20 (C2) and the toolchain here runs 24.
    expect(packageJson().devDependencies['@types/node']).toMatch(/^24\./)
  })

  it('carries no chat dependency — the assistant surface is not in this app', () => {
    // NOA's chat moved to an external LLM UI, so the panel ported in §T.48 drops
    // `@assistant-ui/*` and `assistant-stream`. Named here because a dependency
    // is how that decision would quietly come back.
    const { dependencies, devDependencies } = packageJson()
    const chat = Object.keys({ ...dependencies, ...devDependencies }).filter(
      (name) => name.startsWith('@assistant-ui/') || name === 'assistant-stream',
    )

    expect(chat).toEqual([])
  })
})
