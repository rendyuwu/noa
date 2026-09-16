// @vitest-environment node
// Reads files, renders nothing. jsdom also rewrites `import.meta.url` off the file scheme.
import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * No control in this app submits a form, and nothing here may reintroduce one.
 *
 * MEASURED, and the reason `login-form.tsx` is built the way it is: an operator arrives from the
 * embed's 401 link-out, and that tab is top-level but inherits the frame's sandbox, where
 * `allow-forms` is absent at both of LibreChat's render sites. A sandboxed document turns back at
 * the sandbox check *before* the `submit` event fires, so a `type="submit"` button is silently
 * inert there — the browser logs `Blocked form submission to ''` and the operator sees a button
 * that does nothing. The sandbox survives same-origin navigation, so every page reached from that
 * tab is affected, not only the login screen. That is how it reached the WHM "add server" dialog.
 *
 * The rule the whole app follows: the primary action is a `type="button"` with an `onClick` that
 * calls the same react-hook-form handler the `<form onSubmit>` calls. The `<form>` stays — it costs
 * nothing and keeps the handler in one place — but it is never the only trigger.
 *
 * This is a source scan rather than a render, because the shape is what matters and there is no
 * dialog to render for the form somebody adds next month. Two attributes are banned:
 * `type="submit"` outright, and `form="..."`, which associates a button with a form it does not
 * live inside — and a `<button>` with no `type` inside a form defaults to submit anyway, which is
 * why the ban is on the association, not on the word.
 *
 * **What this does NOT bind**: that each Save button is wired to the right handler. Nothing here
 * renders anything. The dialogs' own specs cover the wiring by clicking the button
 * (`whm-servers-page.test.tsx`, `mint-token-dialog.test.tsx`, `create-role-dialog.test.tsx`,
 * `role-detail-drawer.test.tsx`, `user-detail-drawer.test.tsx`). The Proxmox and PMG dialogs have
 * no such click test — stated rather than left to read as coverage.
 *
 * Enter is the other half of the same rule, and it is bound below by counting rather than by
 * rendering: a form with no submit button gets nothing from the browser's implicit submission, so
 * every `<form>` here carries `submitOnEnter` (`src/lib/forms/submit-on-enter.ts`). The count says
 * each form has one; it does not say the handler passed is that form's own.
 *
 * Watched fail: putting `type="submit"` back on the WHM dialog's Save button reddens `no source
 * file carries a submit control` with that path in the message, and dropping the `onKeyDown` from
 * the login form reddens `every form routes Enter to a handler` with that path.
 */

const SRC = fileURLToPath(new URL('.', import.meta.url))

/** `type="submit"`, and the `form="id"` association that makes an outside button submit one. */
const BANNED = /type="submit"|\bform="/

/**
 * A rendered form, anchored at the start of a line: several files in this app explain the rule in
 * prose, and a `<form onSubmit>` inside a comment is not a form.
 */
const FORM_TAG = /^[ \t]*<form[\s>]/gm
const ENTER_HANDLER = /submitOnEnter\(/g

const count = (source: string, pattern: RegExp) => (source.match(pattern) ?? []).length

function tsxFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) return tsxFiles(full)
    if (!entry.name.endsWith('.tsx') || entry.name.includes('.test.')) return []
    return [full]
  })
}

function offenders(files: { path: string; source: string }[]): string[] {
  return files.flatMap(({ path: file, source }) =>
    source
      .split('\n')
      .map((line, index) => ({ line, number: index + 1 }))
      .filter(({ line }) => BANNED.test(line))
      .map(({ line, number }) => `${path.relative(SRC, file)}:${number}: ${line.trim()}`),
  )
}

describe('the primary action is never a form submit', () => {
  const files = tsxFiles(SRC).map((file) => ({ path: file, source: readFileSync(file, 'utf8') }))

  it('reads a non-empty tree, so an empty pass cannot be a green scan of nothing', () => {
    expect(files.length).toBeGreaterThan(20)
  })

  it('no source file carries a submit control', () => {
    expect(offenders(files)).toEqual([])
  })

  it('every form routes Enter to a handler', () => {
    const unwired = files
      .filter(({ source }) => count(source, FORM_TAG) !== count(source, ENTER_HANDLER))
      .map(({ path: file }) => path.relative(SRC, file))
    expect(unwired).toEqual([])
  })

  it('separates — the Enter count notices a form that has none', () => {
    const planted = '  <form onSubmit={submit}>'
    expect(count(planted, FORM_TAG)).toBe(1)
    expect(count(planted, ENTER_HANDLER)).toBe(0)
  })

  it('separates — the scan finds the banned shape when it is there', () => {
    // The negative control. Without it, the empty result above is satisfied by a regex that matches
    // nothing, and this lane would pass against an app full of submit buttons.
    const planted = [
      { path: path.join(SRC, 'planted.tsx'), source: '  <Button type="submit" loading={busy}>' },
      { path: path.join(SRC, 'planted2.tsx'), source: '  <Button form="whm-server-form">' },
    ]
    expect(offenders(planted)).toEqual([
      'planted.tsx:1: <Button type="submit" loading={busy}>',
      'planted2.tsx:1: <Button form="whm-server-form">',
    ])
  })
})
