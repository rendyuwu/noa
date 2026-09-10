import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { ESLint } from 'eslint'
import { beforeAll, describe, expect, it } from 'vitest'

/**
 * The import firewall in `eslint.config.mjs` — one repo, three independent deploy
 * packages; port, never import from `noa-old`; copied, not rewritten.
 *
 * Two boundaries live in that config and nowhere else in the code:
 *
 *  - `apps/web-embed` is a separate package. The two web apps share no source.
 *  - `noa-old` is a reference repo. The admin port COPIES its files; a copy that keeps an
 *    import back to the original is not a copy, and the path only resolves on
 *    the machine that has both trees checked out side by side.
 *
 * A lint rule nobody exercises is not a control — a typo in a glob, a rule
 * dropped during a config refactor, and the boundary is gone with a green build.
 * So this runs the real ESLint over source text that tries each forbidden
 * specifier, and pairs it with a negative control: an in-app import that must
 * stay clean, otherwise a rule that rejected everything would read as a pass.
 */

const APP_ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const FIXTURE = path.join(APP_ROOT, 'src', 'import-firewall-fixture.ts')

const eslint = new ESLint({ cwd: APP_ROOT })

async function restrictedImportMessages(source: string): Promise<string[]> {
  const [result] = await eslint.lintText(source, { filePath: FIXTURE })

  return (result?.messages ?? [])
    .filter((message) => message.ruleId === 'no-restricted-imports')
    .map((message) => message.message)
}

describe('one repo, three packages; port, never import — the import firewall rejects the boundaries it names', () => {
  // The first `lintText` in this file loads the whole flat config — Next's plugin,
  // typescript-eslint, the rest — and measured here that first call costs ~1.2s
  // against ~20ms for every call after it. Left implicit, that one-off is charged
  // to whichever case runs first, which then fails on the 5s default while
  // measuring nothing about the boundary it names: CI timed out on
  // `refuses ../../web-embed/src/lib/proxy/routes` and passed on retry.
  //
  // So the load is paid here, where a timeout says "the linter was slow to start"
  // instead of "the firewall is broken", and where the budget can be generous
  // without loosening the assertions. The cases below keep the 5s default and now
  // run in tens of milliseconds.
  beforeAll(async () => {
    await restrictedImportMessages('export default 1\n')
  }, 60_000)

  it.each([
    ['../../web-embed/src/lib/proxy/routes', 'apps/web-embed'],
    ['../../../noa-old/apps/web-bigsu/src/lib/nav', 'noa-old'],
    ['../../../noa-old/apps/web-bigsu/src/components/admin/users/users-page', 'noa-old'],
  ])('refuses %s', async (specifier: string) => {
    const messages = await restrictedImportMessages(`import x from '${specifier}'\nexport default x\n`)

    expect(messages.length, `${specifier} was not restricted`).toBeGreaterThan(0)
  })

  it('names the reason, so the error explains the boundary rather than just blocking', async () => {
    const embed = await restrictedImportMessages(
      "import x from '../../web-embed/src/lib/proxy/routes'\nexport default x\n",
    )
    const old = await restrictedImportMessages(
      "import x from '../../../noa-old/apps/web-bigsu/src/lib/nav'\nexport default x\n",
    )

    expect(embed.join(' ')).toMatch(/independent packages/)
    expect(old.join(' ')).toMatch(/reference, not a dependency/)
  })

  it('separates — an in-app import stays clean', async () => {
    // The negative control. A rule that restricted every specifier would satisfy
    // every assertion above while making the package unbuildable.
    const messages = await restrictedImportMessages(
      "import { NAV_ITEMS } from '@/lib/nav'\nexport default NAV_ITEMS\n",
    )

    expect(messages).toEqual([])
  })
})
