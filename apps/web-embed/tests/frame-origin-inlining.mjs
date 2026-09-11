#!/usr/bin/env node
/**
 * The frame origin actually survives `next build` into the shipped server.
 *
 * `pnpm test:inlining`. Not a Vitest spec and not in `pnpm test`: it runs a real `next build` and
 * boots the real standalone server, which is minutes rather than seconds. Run it when anything in
 * `config/framing.ts`, `src/lib/embed/frame-origin.ts` or this app's Dockerfile changes.
 *
 * ## Why this exists at all, and why it is shaped like this
 *
 * The sizing message's target origin comes from `NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN`, and only a
 * variable carrying that prefix is compiled into the output. Point the resolver at the private
 * `NOA_LIBRECHAT_ORIGIN` instead — the name every build, compose file and pipeline already sets,
 * and therefore the likeliest edit anyone makes here — and nothing announces it: the value is
 * `undefined` in the deployed container, the resolver answers `null`, and the frame ships stuck at
 * the host's unresized default while every unit test stays green. A unit test cannot tell those two
 * builds apart, because in-process the read is dynamic either way and the test sets whichever
 * variable it was written to set.
 *
 * So the claim is end-to-end by necessity, and it is POSITIVE. `data-noa-frame-size="measuring"` is
 * reachable only where `resolveFrameTargetOrigin()` returned non-null
 * (`src/components/frame-sizer.tsx`), so with both variables removed from the runtime environment it
 * is true only if the value was baked in.
 *
 * **Two earlier shapes of this check were green against the break.** A grep of `.next/` for the
 * origin matched an unmodified tree, because `buildFramingHeaders` bakes the header's value into the
 * routes manifest and `DEFAULT_LIBRECHAT_ORIGIN` is a hardcoded constant bundled regardless — a grep
 * cannot separate those two bakes from the one under test. Then a NEGATIVE assertion under a wholly
 * empty environment passed vacuously: `getBackendBaseUrl` throws without `NOA_API_URL`, the page
 * fell back to its unavailable surface, and that surface renders no `data-noa-frame-size` attribute
 * at all — the setup gate had become the thing under test. Hence the stub below, and hence the
 * liveness assertion one layer under the subject.
 *
 * ## What this has been watched failing at, and what it did NOT catch
 *
 * Measured on Next 16.3.0 with Turbopack, by mutating the production code and re-running:
 *
 * - resolver reading `process.env.NOA_LIBRECHAT_ORIGIN` (the private name) → RED, "the card shipped
 *   without a resolved target origin".
 * - `resolveFrameAncestor` reading the public name → RED on the header assertion, serving
 *   `frame-ancestors https://chat.a3probe.test` where the private probe was expected. That is the
 *   cross-feed the two-variable split exists to prevent, and it is why the header is asserted here
 *   rather than left to the unit lane.
 * - resolver reading `process.env[A_CONST]`, and `const env = process.env` then `env[A_CONST]` →
 *   both GREEN, because Turbopack constant-folds a statically resolvable key. Recorded because it
 *   is the shape most likely to be reached for, and because a reader who assumes this check guards
 *   the read's *spelling* would be wrong: what it guards is the variable's prefix. If this ever
 *   moves back to webpack, that spelling starts to matter and this note is the reason to re-measure.
 *
 * ## Three origins, so every state is attributable
 *
 * The header and the message target read two different variables on purpose, and this run gives
 * each a value that appears nowhere else — neither equal to the other, neither equal to the pinned
 * development default. That makes the two bakes separable: the served CSP must name the private
 * probe and only it, so a `measuring` attribute cannot be explained by the header's bake, by the
 * development constant, or by the two names cross-feeding.
 *
 * ## Readiness sits below the subject, twice
 *
 * Both waits are for an open TCP socket, never for `/approvals/...` or `/healthz`. A readiness gate
 * pointed at the route under test turns that route's failure into a startup timeout, and a timeout
 * names nothing.
 */

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { cp, rm } from 'node:fs/promises'
import { connect } from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const APP_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)))

/**
 * The origin baked for the sizing message, and the one whose survival is the subject.
 *
 * A `.test` TLD and a name that appears in no fixture, no default and no pipeline: if this string
 * reaches the running server, the only path it can have travelled is the inlining.
 */
const MESSAGE_PROBE_ORIGIN = 'https://chat.a3probe.test'

/** The origin baked for `frame-ancestors`, deliberately NOT the one above. */
const CSP_PROBE_ORIGIN = 'https://chat.cspprobe.test'

/** What `config/framing.ts` falls back to. Neither probe may ever equal it. */
const DEVELOPMENT_DEFAULT = 'https://chat.noa.internal'

/** Shared with the Playwright lane's stub, which already serves exactly the card needed here. */
const PENDING_ID = '9f1c2b7e-0000-4000-8000-000000000001'

/** The tool that card names. The liveness assertion: a broken stub reads as a broken stub. */
const PENDING_TOOL = 'whm_suspend_account'

function port(variable, fallback) {
  const value = Number(process.env[variable])
  return Number.isInteger(value) && value > 0 && value < 65_536 ? value : fallback
}

const STUB_PORT = port('NOA_INLINING_STUB_PORT', 8123)
const APP_PORT = port('NOA_INLINING_APP_PORT', 3123)

/** @type {import('node:child_process').ChildProcess[]} */
const children = []

function run(command, args, { env = {}, cwd = APP_DIR, inherit = true } = {}) {
  const child = spawn(command, args, {
    cwd,
    env: { ...process.env, ...env },
    stdio: inherit ? 'inherit' : ['ignore', 'pipe', 'pipe'],
  })
  children.push(child)
  return child
}

function finished(child, label) {
  return new Promise((resolve, reject) => {
    child.on('error', reject)
    child.on('exit', (code) =>
      code === 0 ? resolve() : reject(new Error(`${label} exited with ${code}`)),
    )
  })
}

/** One TCP connect attempt. Resolves true when something is listening. */
function knocks(host, target) {
  return new Promise((resolve) => {
    const socket = connect({ host, port: target })
    const done = (answer) => {
      socket.destroy()
      resolve(answer)
    }
    socket.once('connect', () => done(true))
    socket.once('error', () => done(false))
    socket.setTimeout(500, () => done(false))
  })
}

/**
 * Wait for a socket, and fail immediately if the process behind it has already died.
 *
 * The early-exit check is the difference between "the server printed a stack trace and quit" and a
 * flat timeout that names nothing.
 */
async function listening(child, target, label, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`${label} exited with ${child.exitCode}`)
    if (await knocks('127.0.0.1', target)) return
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error(`${label} never opened 127.0.0.1:${target} within ${timeoutMs}ms`)
}

function stop() {
  for (const child of children) {
    if (child.exitCode === null) child.kill('SIGKILL')
  }
}

async function main() {
  assert.notEqual(MESSAGE_PROBE_ORIGIN, CSP_PROBE_ORIGIN)
  assert.notEqual(MESSAGE_PROBE_ORIGIN, DEVELOPMENT_DEFAULT)
  assert.notEqual(CSP_PROBE_ORIGIN, DEVELOPMENT_DEFAULT)

  // A stale `.next/standalone` from an earlier build would be a server answering about a tree
  // nobody just compiled.
  await rm(path.join(APP_DIR, '.next'), { recursive: true, force: true })

  process.stdout.write(`\n== building with ${MESSAGE_PROBE_ORIGIN} ==\n`)
  await finished(
    run('pnpm', ['exec', 'next', 'build'], {
      env: {
        NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN: MESSAGE_PROBE_ORIGIN,
        NOA_LIBRECHAT_ORIGIN: CSP_PROBE_ORIGIN,
      },
    }),
    'next build',
  )

  // `output: 'standalone'` emits a server plus its traced dependencies and nothing else; the static
  // assets are the deploy step's to place, and the Dockerfile does exactly this.
  await cp(
    path.join(APP_DIR, '.next/static'),
    path.join(APP_DIR, '.next/standalone/.next/static'),
    { recursive: true },
  )

  process.stdout.write('\n== starting the stub upstream ==\n')
  const stub = run('node', ['e2e/support/upstream-stub.mjs'], {
    env: { UPSTREAM_STUB_PORT: String(STUB_PORT), STUB_PENDING_ID: PENDING_ID },
  })
  await listening(stub, STUB_PORT, 'upstream stub', 15_000)

  process.stdout.write('\n== starting the standalone server, both framing variables unset ==\n')
  const runtimeEnv = { ...process.env }
  delete runtimeEnv.NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN
  delete runtimeEnv.NOA_LIBRECHAT_ORIGIN

  const server = spawn('node', ['.next/standalone/server.js'], {
    cwd: APP_DIR,
    stdio: 'inherit',
    env: {
      ...runtimeEnv,
      PORT: String(APP_PORT),
      HOSTNAME: '127.0.0.1',
      NODE_ENV: 'production',
      // Named, not defaulted. Without it `getBackendBaseUrl` throws, `loadApprovalCard` reports the
      // API unreachable, and the page renders a surface carrying no `data-noa-frame-size` at all —
      // which is how the previous shape of this check passed while proving nothing.
      NOA_API_URL: `http://127.0.0.1:${STUB_PORT}`,
    },
  })
  children.push(server)
  await listening(server, APP_PORT, 'standalone server', 30_000)

  const response = await fetch(`http://127.0.0.1:${APP_PORT}/approvals/${PENDING_ID}`)
  const html = await response.text()

  // Liveness first, one layer below the subject: a stub that stopped serving this card, or a route
  // that 500s, must read as that rather than as a failed inlining.
  assert.equal(response.status, 200, `the approvals page answered ${response.status}`)
  assert.ok(
    html.includes(PENDING_TOOL),
    `the page did not render the stub's PENDING card (no "${PENDING_TOOL}" in the HTML)`,
  )

  // The header's bake, which is a separate variable read at a separate place. Asserting it names the
  // CSP probe and only it is what makes the next assertion attributable: the message target cannot
  // have come from the header's value, from the development constant, or from the private name.
  assert.equal(
    response.headers.get('content-security-policy'),
    `frame-ancestors ${CSP_PROBE_ORIGIN}`,
    'the framing header did not carry the private name baked into this build',
  )

  // The subject. `measuring` is reachable only where the resolver answered non-null, and the
  // runtime environment above holds neither framing variable, so a non-null answer can only have
  // been compiled in.
  assert.ok(
    html.includes('data-noa-frame-size="measuring"'),
    'the card shipped without a resolved target origin: the NEXT_PUBLIC_ value was not inlined',
  )
  assert.ok(
    !html.includes('no-target-origin'),
    'the card rendered the unconfigured state despite a build-time origin',
  )

  process.stdout.write('\nOK — the build-time frame origin reached the shipped server.\n')
}

main()
  .then(() => {
    stop()
    process.exit(0)
  })
  .catch((error) => {
    process.stderr.write(`\nFAILED — ${error.message}\n`)
    stop()
    process.exit(1)
  })
