import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { findUp, loadRootEnv, parseEnvFile } from './root-env'

/**
 * The repo-root `.env` loader (§T.44, C11).
 *
 * Built against a temporary tree rather than the real repo: the real `.env` is gitignored, so a
 * test that read it would pass or fail on whatever a developer happens to have configured.
 */

let root: string
let app: string

beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'noa-root-env-'))
  mkdirSync(path.join(root, '.git'))
  app = path.join(root, 'apps', 'web-embed')
  mkdirSync(app, { recursive: true })
})

afterEach(() => {
  rmSync(root, { recursive: true, force: true })
})

describe('parseEnvFile', () => {
  it('reads key/value pairs, skipping comments and blank lines', () => {
    expect(
      parseEnvFile(['# a comment', '', 'NOA_API_URL=http://localhost:8000', '   ', 'B=2'].join('\n')),
    ).toEqual([
      ['NOA_API_URL', 'http://localhost:8000'],
      ['B', '2'],
    ])
  })

  it('strips a surrounding pair of quotes and an `export` prefix', () => {
    expect(parseEnvFile(['export A="one"', "B='two'"].join('\n'))).toEqual([
      ['A', 'one'],
      ['B', 'two'],
    ])
  })

  it('keeps a JSON array value intact — C11 spells list env vars that way', () => {
    // `AUTH_BOOTSTRAP_ADMIN_EMAILS=["a@b.com"]`: the brackets are the value, and a
    // loader that unquoted per-character would hand the app a broken list.
    expect(parseEnvFile('AUTH_BOOTSTRAP_ADMIN_EMAILS=["a@b.com"]')).toEqual([
      ['AUTH_BOOTSTRAP_ADMIN_EMAILS', '["a@b.com"]'],
    ])
  })

  it('keeps an `=` that appears inside the value', () => {
    expect(parseEnvFile('POSTGRES_URL=postgresql://u:p@h/db?opt=1')).toEqual([
      ['POSTGRES_URL', 'postgresql://u:p@h/db?opt=1'],
    ])
  })

  it('ignores a line with no `=` rather than inventing an empty value', () => {
    expect(parseEnvFile(['NOT_AN_ASSIGNMENT', 'A=1'].join('\n'))).toEqual([['A', '1']])
  })
})

describe('findUp', () => {
  it('finds the repo root from the app directory', () => {
    expect(findUp(app, '.git')).toBe(root)
  })

  it('returns null when the marker is nowhere above the start', () => {
    expect(findUp(app, 'this-marker-does-not-exist')).toBeNull()
  })
})

describe('loadRootEnv', () => {
  it('fills a key the environment does not already have', () => {
    writeFileSync(path.join(root, '.env'), 'NOA_API_URL=http://localhost:8000\n')
    const env: Record<string, string | undefined> = {}

    loadRootEnv(app, env)

    expect(env['NOA_API_URL']).toBe('http://localhost:8000')
  })

  it('never overwrites a key already set in the real environment', () => {
    // Load-bearing, not a nicety: the Playwright run points `NOA_API_URL` at its own
    // stub upstream through this precedence. If the file won, the e2e would measure
    // whatever API a developer happens to be running.
    writeFileSync(path.join(root, '.env'), 'NOA_API_URL=http://localhost:8000\n')
    const env: Record<string, string | undefined> = { NOA_API_URL: 'http://127.0.0.1:8099' }

    loadRootEnv(app, env)

    expect(env['NOA_API_URL']).toBe('http://127.0.0.1:8099')
  })

  it('treats a key set to empty string as set — `in` is the test, not truthiness', () => {
    writeFileSync(path.join(root, '.env'), 'NOA_API_URL=http://localhost:8000\n')
    const env: Record<string, string | undefined> = { NOA_API_URL: '' }

    loadRootEnv(app, env)

    // An empty value is a deliberate unset by the operator. Filling it from the file
    // would silently re-point the proxy at an API they turned off.
    expect(env['NOA_API_URL']).toBe('')
  })

  it('does nothing when the repo root holds no .env', () => {
    const env: Record<string, string | undefined> = {}

    loadRootEnv(app, env)

    expect(env).toEqual({})
  })

  it('does nothing when there is no .git above the start directory', () => {
    const orphan = mkdtempSync(path.join(tmpdir(), 'noa-orphan-'))
    writeFileSync(path.join(root, '.env'), 'NOA_API_URL=http://localhost:8000\n')
    const env: Record<string, string | undefined> = {}

    try {
      loadRootEnv(orphan, env)
      expect(env).toEqual({})
    } finally {
      rmSync(orphan, { recursive: true, force: true })
    }
  })
})
