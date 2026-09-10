import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'

/**
 * Repo-root `.env` loading for local dev.
 *
 * Next only auto-loads env files from the app directory, and this package deliberately has no
 * `.env.local`: two env files for one deployment is two places for `NOA_API_URL` to disagree, and
 * a proxy silently talking to the wrong API is not a failure anyone would see. So the repo-root
 * file is the single source, read here and applied by `next.config.ts`.
 *
 * Lives in its own module rather than inside the config because the precedence rule below is the
 * part that can be got wrong, and a rule inside `next.config.ts` cannot be tested without
 * importing the config's side effects.
 */

/** Walk up from `start` until a directory containing `marker` is found. */
export function findUp(start: string, marker: string): string | null {
  let directory = start
  for (;;) {
    if (existsSync(path.join(directory, marker))) return directory
    const parent = path.dirname(directory)
    if (parent === directory) return null
    directory = parent
  }
}

function unquote(value: string): string {
  if (value.length < 2) return value
  const quoted =
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  return quoted ? value.slice(1, -1) : value
}

/** Parse `.env` text into key/value pairs. Comments and blank lines are skipped. */
export function parseEnvFile(contents: string): Array<[string, string]> {
  const out: Array<[string, string]> = []

  for (const rawLine of contents.split('\n')) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) continue

    const eq = line.indexOf('=')
    if (eq === -1) continue

    const key = line.slice(0, eq).replace(/^export\s+/, '').trim()
    if (!key) continue

    out.push([key, unquote(line.slice(eq + 1).trim())])
  }

  return out
}

/**
 * Fill `env` from the repo-root `.env`, without overwriting anything already set.
 *
 * The precedence is load-bearing: a real shell or CI variable wins, which is what lets the
 * Playwright run point `NOA_API_URL` at its own stub upstream instead of whatever a developer's
 * `.env` says. No-op when there is no `.git` above this package or no `.env` beside it —
 * production supplies env via ConfigMaps/Secrets, and `output: 'standalone'` never executes the
 * Next config at runtime anyway.
 */
export function loadRootEnv(start: string, env: Record<string, string | undefined>): void {
  const repoRoot = findUp(start, '.git')
  if (!repoRoot) return

  const envPath = path.join(repoRoot, '.env')
  if (!existsSync(envPath)) return

  for (const [key, value] of parseEnvFile(readFileSync(envPath, 'utf8'))) {
    if (key in env) continue
    env[key] = value
  }
}
