import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * File-size hygiene: `.ts` <= 300 lines, `.tsx` <= 450.
 *
 * Scans `git ls-files` rather than walking the filesystem — the same choice
 * `apps/api/tests/test_config.py` makes. A walk would eat `.next/`, generated
 * type stubs and anything a developer left lying around, so it would report
 * failures nobody can act on.
 *
 * `--others --exclude-standard` widens it to files that are not staged yet: a
 * cap that only sees `git add`ed files first goes red at commit time, which is
 * after the author has stopped looking. Ignored paths stay out either way.
 */

const APP_ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

const LIMITS: Record<string, number> = {
  '.ts': 300,
  '.tsx': 450,
}

function trackedFiles(): string[] {
  const args = ['ls-files', '-z', '--cached', '--others', '--exclude-standard']
  const stdout = execFileSync('git', args, { cwd: APP_ROOT, encoding: 'utf8' })

  return stdout.split('\0').filter((entry) => entry.length > 0)
}

function lineCount(relativePath: string): number {
  return readFileSync(path.join(APP_ROOT, relativePath), 'utf8').split('\n').length
}

describe('C14/V65 — TypeScript file size limits', () => {
  it('scans the package it is meant to bound', () => {
    // Without this the suite below is a tautology: an empty file list satisfies
    // every limit, so a broken `git ls-files` call would read as compliance.
    const scanned = trackedFiles().filter((file) => path.extname(file) in LIMITS)

    expect(scanned.length).toBeGreaterThan(0)
    expect(scanned).toContain('src/app/healthz/route.ts')
  })

  it('keeps every tracked .ts and .tsx under its limit', () => {
    const oversized = trackedFiles()
      .filter((file) => path.extname(file) in LIMITS)
      .map((file) => ({ file, lines: lineCount(file), limit: LIMITS[path.extname(file)]! }))
      .filter(({ lines, limit }) => lines > limit)
      .map(({ file, lines, limit }) => `${file}: ${lines} lines > ${limit}`)

    expect(oversized).toEqual([])
  })

  it('separates — the limits are the ones C14 states', () => {
    // The comparison above is only as good as these two numbers. Pinning them
    // means loosening a cap has to be an edit to this line, not a quiet drift.
    expect(LIMITS).toEqual({ '.ts': 300, '.tsx': 450 })
  })
})
