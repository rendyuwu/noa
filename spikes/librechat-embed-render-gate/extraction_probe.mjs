/**
 * Stage 4 of the standalone surface probe: can a document inside the mcp-ui iframe hand BYTES to
 * the operator, with nothing changed on the host side?
 *
 * `resize_probe.mjs` already measured the async Clipboard API (`navigator.clipboard.write` with an
 * `image/png`), and `<a download>`. Both are refused. This module asks the six remaining ways out,
 * at the same two sandbox strings the package was measured to emit, in the same cross-origin frame:
 *
 *   4a. `document.execCommand('copy')` over a script-made DOM selection — the legacy path, gated
 *       by user activation rather than by Permissions-Policy, so its answer does not follow from
 *       the async API's.
 *   4b. `navigator.clipboard.writeText` — same policy as `write`, different entry point.
 *   4c. `document.execCommand('copy')` over a selection containing an `<img>`, to see which
 *       clipboard FLAVOURS arrive (an `image/png` payload, or only markup).
 *   4d. the zero-code path: the operator drag-selects the text and presses Ctrl+C.
 *   4e. dragging an `<img>` out of the frame onto the host document.
 *   4f. `target="_blank"`, at the string that grants `allow-popups` and at the one that does not,
 *       and what the opened tab inherited.
 *
 * The discipline is the one the rest of the file keeps. Nothing is concluded from a call not
 * throwing: `document.execCommand('copy')` returns `true` in Chromium whenever the editing command
 * dispatched, which is a different claim from "the clipboard changed", so every clipboard step
 * SEEDS the clipboard with a sentinel from a permitted context, verifies the sentinel is what is
 * there, performs the act, and then READS the clipboard back from a permitted context. Two
 * independent readers are used where both are available — a top-level page with `clipboard-read`
 * granted, and `xclip` talking to the X server outside the browser entirely — and which one
 * answered is recorded beside the answer.
 *
 * Controls, per measurement:
 *   - a top-level (unframed) page runs the identical steps. Where it fails too, the posture is
 *     named as unable to answer instead of counted as agreement.
 *   - the seeded sentinel read back BEFORE each act is the proof the reader can tell the two
 *     strings apart; without it "the clipboard still holds the sentinel" would be unfalsifiable.
 *   - the drag-out claim is paired with a drop target INSIDE the frame: a drag that lands there
 *     and not on the host isolates the frame boundary from the drag synthesis.
 *   - the popup claim is paired with the sandbox string that lacks `allow-popups` (0 tabs, the
 *     refusal is silent) and with an UNSANDBOXED cross-origin frame whose popup must be able to
 *     download — otherwise "the popup could not download" would just be Chromium's own rules.
 */

import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';

/** `xclip` reads the X CLIPBOARD selection from outside the browser process, so it is an oracle
 * the page's Permissions-Policy and Playwright's permission grants cannot influence. It needs a
 * display, which means it only exists on a `--headed` run under Xvfb. */
const XCLIP = '/usr/bin/xclip';
const XCLIP_AVAILABLE = Boolean(process.env.DISPLAY) && existsSync(XCLIP);

/** This stage is minutes long and most of it is silent, so it narrates. Without this a stall is
 * indistinguishable from slow work, and the only way to find out which is to kill the run. */
const trace = (message) => console.log(`      · ${message}`);

/** Every clipboard call is raced against an in-PAGE timer rather than a Node-side one.
 *
 * A `page.evaluate` has no timeout: an awaited clipboard promise that never settles hangs the run
 * with no output and nothing to read afterwards. Racing inside the page guarantees the evaluate
 * returns, and the timeout lands in the artifact as a recorded string — which is the same rule the
 * rest of the probe follows, one failure mode over. */
const CLIPBOARD_CALL_TIMEOUT_MS = 4000;

function xclipRun(args, opts = {}) {
  try {
    return { ok: true, out: execFileSync(XCLIP, args, { timeout: 5000, stdio: ['ignore', 'pipe', 'pipe'], ...opts }) };
  } catch (error) {
    const stderr = error && error.stderr ? String(error.stderr).trim() : '';
    return { ok: false, error: `exit ${error && error.status}: ${(stderr || String(error && error.message)).slice(0, 200)}` };
  }
}

function xclipSeed(text) {
  try {
    // xclip forks and the child keeps owning the selection; stdout/stderr are dropped so the
    // parent's exit is not held open by the inherited pipe.
    execFileSync(XCLIP, ['-selection', 'clipboard', '-i'], { input: text, timeout: 5000, stdio: ['pipe', 'ignore', 'ignore'] });
    return 'ok';
  } catch (error) {
    return `failed: ${String(error && error.message).slice(0, 160)}`;
  }
}

function xclipRead() {
  const targets = xclipRun(['-selection', 'clipboard', '-t', 'TARGETS', '-o'], { encoding: 'utf8' });
  const targetList = targets.ok
    ? targets.out.split('\n').map((s) => s.trim()).filter(Boolean)
    : [`(TARGETS unavailable: ${targets.error})`];
  const text = xclipRun(['-selection', 'clipboard', '-o'], { encoding: 'utf8' });
  const out = {
    targets: targetList,
    text: text.ok ? text.out : `(unavailable: ${text.error})`,
    pngBytes: -1,
    htmlBytes: -1,
  };
  if (targetList.includes('image/png')) {
    const png = xclipRun(['-selection', 'clipboard', '-t', 'image/png', '-o']);
    out.pngBytes = png.ok ? png.out.byteLength : -1;
  }
  if (targetList.includes('text/html')) {
    const html = xclipRun(['-selection', 'clipboard', '-t', 'text/html', '-o'], { encoding: 'utf8' });
    out.htmlBytes = html.ok ? Buffer.byteLength(html.out) : -1;
    out.htmlHead = html.ok ? html.out.replace(/\s+/g, ' ').slice(0, 300) : null;
    // Whether the markup flavour carries the image by REFERENCE, and under which scheme. A
    // `blob:` src is scoped to the frame's own document, so markup that only points at one has
    // handed the operator nothing they can paste anywhere else.
    out.htmlImgTagCount = html.ok ? (html.out.match(/<img/gi) ?? []).length : -1;
    out.htmlImgSrcSchemes = html.ok
      ? [...html.out.matchAll(/<img[^>]*src="([a-zA-Z][a-zA-Z0-9+.-]*):/g)].map((m) => m[1])
      : null;
  }
  return out;
}

/** The in-browser reader: a top-level page at the HOST origin. It is deliberately NOT the frame's
 * origin, so granting it `clipboard-read` cannot be what lets a subject frame write. */
async function readViaBrowser(reader) {
  await reader.bringToFront();
  await reader.mouse.click(6, 6);
  return reader.evaluate(async (timeoutMs) => {
    const out = { text: null, textError: null, itemTypes: null, readError: null, pngBytes: -1 };
    const deadline = () => new Promise((resolve) => setTimeout(() => resolve('__timeout__'), timeoutMs));
    const textOrTimeout = await Promise.race([
      navigator.clipboard.readText().then((v) => ({ v }), (e) => ({ e: `${e && e.name}: ${e && e.message}` })),
      deadline(),
    ]);
    if (textOrTimeout === '__timeout__') out.textError = `(readText did not settle in ${timeoutMs}ms)`;
    else if ('e' in textOrTimeout) out.textError = textOrTimeout.e;
    else out.text = textOrTimeout.v;

    const readOrTimeout = await Promise.race([
      navigator.clipboard.read().then(async (items) => {
        const types = items.map((item) => Array.from(item.types));
        let pngBytes = -1;
        for (const item of items) {
          if (item.types.includes('image/png')) pngBytes = (await item.getType('image/png')).size;
        }
        return { types, pngBytes };
      }, (e) => ({ e: `${e && e.name}: ${e && e.message}` })),
      deadline(),
    ]);
    if (readOrTimeout === '__timeout__') out.readError = `(read did not settle in ${timeoutMs}ms)`;
    else if ('e' in readOrTimeout) out.readError = readOrTimeout.e;
    else {
      out.itemTypes = readOrTimeout.types;
      out.pngBytes = readOrTimeout.pngBytes;
    }
    return out;
  }, CLIPBOARD_CALL_TIMEOUT_MS);
}

/** Which readers this posture actually has.
 *
 * Established ONCE per posture rather than per step, because it is a property of the posture: with
 * no `clipboard-read` grant the in-browser reader never settles, and paying two 4s timeouts on
 * every seed and every readback turns a 70-second matrix into a quarter of an hour. The capability
 * is measured, not assumed from the grant list — a seeded sentinel it can read back is the proof —
 * and the reason it is off is recorded next to every value it did not produce. */
async function probeReaders(reader) {
  const sentinel = `NOA-READER-CHECK-${Date.now()}`;
  // Both seeders, because either one alone would make the check answer the wrong question: with no
  // display there is no xclip to seed with, and a browser reader that works would be switched off
  // for want of a sentinel rather than for want of permission.
  const seedNote = XCLIP_AVAILABLE ? xclipSeed(sentinel) : '(no display: xclip not used)';
  await reader.bringToFront();
  await reader.mouse.click(6, 6);
  let browserSeedNote;
  try {
    browserSeedNote = await reader.evaluate(
      ([s, timeoutMs]) =>
        Promise.race([
          navigator.clipboard.writeText(s).then(() => 'ok', (e) => `rejected ${e && e.name}: ${e && e.message}`),
          new Promise((resolve) => setTimeout(() => resolve(`(did not settle in ${timeoutMs}ms)`), timeoutMs)),
        ]),
      [sentinel, CLIPBOARD_CALL_TIMEOUT_MS],
    );
  } catch (error) {
    browserSeedNote = `threw ${String(error).split('\n')[0]}`;
  }
  const seen = await readViaBrowser(reader);
  const browserLive = seen.text === sentinel;
  return {
    browserReader: browserLive,
    browserReaderNote: browserLive
      ? 'read back the seeded sentinel, so it is a live oracle in this posture'
      : `could not read the seeded sentinel (${seen.textError ?? `got ${JSON.stringify(seen.text)}`}; the browser-side seed said "${browserSeedNote}") — every browser-side clipboard value below is omitted for this posture`,
    browserSeedNote,
    xclipReader: XCLIP_AVAILABLE,
    xclipSeedNote: seedNote,
  };
}

async function readClipboard(reader, readers) {
  const browser = readers.browserReader
    ? await readViaBrowser(reader)
    : { text: null, textError: `(reader off: ${readers.browserReaderNote})`, itemTypes: null, readError: `(reader off: ${readers.browserReaderNote})`, pngBytes: -1 };
  return { browser, xclip: XCLIP_AVAILABLE ? xclipRead() : null };
}

/** Seed, then read back, so a later "unchanged" is a comparison against a value proven to have
 * been there. Both seeders write the SAME sentinel, so they cannot disagree about what the
 * baseline is; which of them took is recorded. */
async function seedClipboard(reader, readers, sentinel) {
  const viaXclip = XCLIP_AVAILABLE ? xclipSeed(sentinel) : '(no display: xclip not used)';
  await reader.bringToFront();
  await reader.mouse.click(6, 6);
  let viaBrowser;
  try {
    viaBrowser = await reader.evaluate(
      ([s, timeoutMs]) =>
        Promise.race([
          navigator.clipboard.writeText(s).then(() => 'ok', (e) => `rejected ${e && e.name}: ${e && e.message}`),
          new Promise((resolve) => setTimeout(() => resolve(`(did not settle in ${timeoutMs}ms)`), timeoutMs)),
        ]),
      [sentinel, CLIPBOARD_CALL_TIMEOUT_MS],
    );
  } catch (error) {
    viaBrowser = `threw ${String(error).split('\n')[0]}`;
  }
  const baseline = await readClipboard(reader, readers);
  const seenByBrowser = baseline.browser.text === sentinel;
  const seenByXclip = Boolean(baseline.xclip && baseline.xclip.text === sentinel);
  return {
    sentinel,
    viaXclip,
    viaBrowser,
    baseline,
    verifiedBy: [seenByBrowser ? 'browser-readText' : null, seenByXclip ? 'xclip' : null].filter(Boolean),
  };
}

/** What a clipboard step measured: the value the seed proved was there, and the value after. */
function clipboardDelta(seed, after, payload) {
  const browserHas = typeof after.browser.text === 'string' && after.browser.text.includes(payload);
  const xclipHas = Boolean(after.xclip && typeof after.xclip.text === 'string' && after.xclip.text.includes(payload));
  const browserStillSentinel = after.browser.text === seed.sentinel;
  const xclipStillSentinel = Boolean(after.xclip && after.xclip.text === seed.sentinel);
  return {
    expectedPayload: payload,
    seedVerifiedBy: seed.verifiedBy,
    clipboardChanged: browserHas || xclipHas,
    payloadSeenBy: [browserHas ? 'browser-readText' : null, xclipHas ? 'xclip' : null].filter(Boolean),
    stillHoldsSentinel: browserStillSentinel || xclipStillSentinel,
    browserReadText: after.browser.text,
    browserReadTextError: after.browser.textError,
    browserItemTypes: after.browser.itemTypes,
    browserReadError: after.browser.readError,
    browserPngBytes: after.browser.pngBytes,
    xclipTargets: after.xclip ? after.xclip.targets : null,
    xclipText: after.xclip ? after.xclip.text : null,
    xclipPngBytes: after.xclip ? after.xclip.pngBytes : -1,
    xclipHtmlHead: after.xclip ? (after.xclip.htmlHead ?? null) : null,
    xclipHtmlImgTagCount: after.xclip ? (after.xclip.htmlImgTagCount ?? -1) : -1,
    xclipHtmlImgSrcSchemes: after.xclip ? (after.xclip.htmlImgSrcSchemes ?? null) : null,
  };
}

const probeState = (scope) => scope.locator('#probe').evaluate((el) => ({ ...el.dataset }));

async function frameFor(page, url, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const frame = page.frames().find((f) => f.url() === url);
    if (frame) return frame;
    if (Date.now() > deadline) throw new Error(`frame never appeared for ${url}`);
    await page.waitForTimeout(100);
  }
}

/** A native HTML5 drag, synthesised as a real pointer path. Playwright turns a pressed-button
 * mouse move into Chromium's drag machinery, so this is the same event sequence an operator
 * produces — which matters, because a `dispatchEvent`-built `dragstart` would answer a question
 * about synthetic events instead of about the frame boundary. */
async function dragBetween(page, from, to) {
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + from.width / 2 + 10, from.y + from.height / 2 + 10, { steps: 5 });
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 15 });
  await page.mouse.move(to.x + to.width / 2 + 5, to.y + to.height / 2 + 5, { steps: 5 });
  await page.mouse.up();
  await page.waitForTimeout(400);
}

/** Everything measurable inside a tab opened from the frame. */
async function probePopup(popup, sink, label) {
  sink.attach(popup, label);
  await popup.bringToFront();
  await popup.locator('#probe[data-ready="1"]').waitFor({ state: 'attached', timeout: 20_000 });

  const meta = await popup.evaluate(() => ({
    origin: location.origin,
    href: location.href,
    openerIsNull: window.opener === null,
    hasFocus: document.hasFocus(),
    isSecureContext: window.isSecureContext,
    typeofFeaturePolicy: typeof document.featurePolicy,
    ancestorOriginCount: location.ancestorOrigins ? location.ancestorOrigins.length : -1,
  }));

  await popup.locator('#clip').click();
  await popup.waitForTimeout(1200);
  await popup.locator('#write-text').click();
  await popup.waitForTimeout(800);

  const waiting = popup
    .waitForEvent('download', { timeout: 4000 })
    .then(async (d) => ({ fired: true, suggestedFilename: d.suggestedFilename(), failure: await d.failure() }))
    .catch((error) => ({ fired: false, timeoutError: String(error).split('\n')[0] }));
  await popup.locator('#dl').click();
  const download = await waiting;
  await popup.waitForTimeout(300);

  const state = await probeState(popup);
  return {
    meta,
    clipboardWriteImagePng: {
      result: state.clipResult ?? '(not run)',
      errorName: state.clipErrorName ?? null,
      errorMessage: state.clipError ?? null,
    },
    clipboardWriteText: {
      result: state.writeTextResult ?? '(not run)',
      errorName: state.writeTextErrorName ?? null,
      errorMessage: state.writeTextError ?? null,
    },
    download: { anchorClicked: state.dlAnchorClicked ?? '(not clicked)', ...download },
    consoleInPopup: sink.drain(label),
  };
}

async function runVariant({ context, reader, readers, variant, framedUrl, hostOrigin, token, sink, keepOpen }) {
  const url = `${framedUrl(variant.origin, variant.id)}&token=${encodeURIComponent(token)}`;
  const page = await context.newPage();
  sink.attach(page, variant.id);

  let mounted = null;
  let scope;
  if (variant.topLevel) {
    await page.goto(url);
    scope = page;
  } else {
    await page.goto(`${hostOrigin}/host.html`);
    await page.waitForFunction(() => window.__harness && window.__harness.ready === true);
    mounted = await page.evaluate(
      (spec) => window.__harness.mountRaw(spec),
      { slot: variant.id, url, sandbox: variant.sandbox, allow: variant.allow ?? null, width: 460, height: 720 },
    );
    scope = await frameFor(page, url);
  }
  await page.bringToFront();

  // Readiness gates sit below everything under test: the PNG blob exists, and both <img> elements
  // have loaded. Gating on anything a clipboard or drag call touches would turn a refusal into a
  // timeout, and a timeout names nothing.
  await scope.locator('#probe[data-ready="1"]').waitFor({ state: 'attached', timeout: 20_000 });
  await scope.locator('#probe[data-img-ready="1"]').waitFor({ state: 'attached', timeout: 20_000 });
  const atLoad = await probeState(scope);

  const step = async (sentinelTag, act, payload) => {
    trace(`${variant.id}: step ${sentinelTag} — seeding`);
    const seed = await seedClipboard(reader, readers, `NOA-SENTINEL-${sentinelTag}-${variant.id}`);
    await page.bringToFront();
    trace(`${variant.id}: step ${sentinelTag} — acting (seed verified by ${seed.verifiedBy.join('+') || 'nothing'})`);
    const extra = await act();
    trace(`${variant.id}: step ${sentinelTag} — reading back`);
    const after = await readClipboard(reader, readers);
    return { seed: { sentinel: seed.sentinel, viaXclip: seed.viaXclip, viaBrowser: seed.viaBrowser, verifiedBy: seed.verifiedBy }, ...clipboardDelta(seed, after, payload), ...extra };
  };

  // --- 4a: execCommand('copy') over a script-made selection ---
  const execCopy = await step('A', async () => {
    await scope.locator('#exec-copy').click();
    await page.waitForTimeout(600);
    const s = await probeState(scope);
    return {
      inFrame: {
        execCommandReturn: s.execCopyReturn ?? '(not run)',
        execCommandError: s.execCopyError ?? null,
        queryCommandEnabledCopy: s.execCopyQueryEnabled ?? null,
        copyEventFired: s.execCopyEventFired ?? null,
        copyEventTypesSeen: s.copyEventTypes ?? null,
        selectionAtCopy: s.execCopySelection ?? null,
        documentHadFocus: s.execCopyHasFocus ?? null,
      },
    };
  }, atLoad.textPayload);

  // --- 4b: clipboard.writeText ---
  const writeText = await step('B', async () => {
    await scope.locator('#write-text').click();
    await page.waitForTimeout(1200);
    const s = await probeState(scope);
    return {
      inFrame: {
        result: s.writeTextResult ?? '(not run)',
        errorName: s.writeTextErrorName ?? null,
        errorMessage: s.writeTextError ?? null,
        documentHadFocus: s.writeTextHasFocus ?? null,
      },
    };
  }, atLoad.writeTextPayload);

  // --- 4c: execCommand('copy') over a selection containing an <img> ---
  const imageCopy = await step('C', async () => {
    await scope.locator('#exec-copy-image').click();
    await page.waitForTimeout(800);
    const s = await probeState(scope);
    return {
      inFrame: {
        execCommandReturn: s.imgCopyReturn ?? '(not run)',
        execCommandError: s.imgCopyError ?? null,
        copyEventFired: s.imgCopyEventFired ?? null,
        selectionText: s.imgCopySelectionText ?? null,
        selectionContainsImg: s.imgCopySelectionContainsImg ?? null,
        imgNaturalSize: s.imgCopyImgNaturalSize ?? null,
      },
    };
  }, atLoad.imgCopyPayload);

  // --- 4d: the zero-code path — drag-select with the mouse, then Ctrl+C ---
  const manual = await step('D', async () => {
    const box = await scope.locator('#copytext').boundingBox();
    await page.mouse.move(box.x + 8, box.y + box.height / 2);
    await page.mouse.down();
    for (let i = 1; i <= 8; i += 1) {
      await page.mouse.move(box.x + 8 + ((box.width - 16) * i) / 8, box.y + box.height / 2, { steps: 2 });
    }
    await page.mouse.up();
    const selectionText = await scope.evaluate(() => String(window.getSelection()));
    await page.keyboard.press('Control+C');
    await page.waitForTimeout(700);
    return {
      inFrame: {
        selectionAfterMouseDrag: selectionText,
        selectionLength: selectionText.length,
        userSelectComputed: atLoad.userSelect,
        webkitUserSelectComputed: atLoad.webkitUserSelect,
        bodyUserSelectComputed: atLoad.bodyUserSelect,
        copyEventTypesSeen: (await probeState(scope)).copyEventTypes ?? null,
      },
    };
  }, atLoad.textPayload);

  // --- 4g: the async Clipboard API with an image/png, re-run here for one reason only.
  //
  // Stage 3 already measured that this is refused in the frame. It is repeated here because 4c
  // reports the ABSENCE of an `image/png` flavour, and an absence is worth nothing unless the
  // readers are shown able to see that flavour when it IS there. On the unframed control this call
  // resolves, so a run where `image/png` still never appears in the readers' flavour list is a run
  // whose 4c answer must be discarded rather than believed.
  const asyncImageWrite = await step('G', async () => {
    await scope.locator('#clip').click();
    await page.waitForTimeout(1200);
    const s = await probeState(scope);
    return {
      inFrame: {
        result: s.clipResult ?? '(not run)',
        errorName: s.clipErrorName ?? null,
        errorMessage: s.clipError ?? null,
        pngBlobBytes: Number(s.pngBlobBytes ?? -1),
      },
    };
  }, '(no text payload: this step is about the image flavour)');

  // --- 4f: target="_blank" ---
  //
  // Deliberately BEFORE the drag. The first run of this stage had the drag first and every popout
  // click after a drag-to-host that delivered nothing came back with the frame's own click handler
  // never having fired — while the one variant whose host drop DID land clicked normally. A drag
  // the harness cannot complete leaves Chromium believing one is still in flight and the next
  // synthesised click is swallowed. That is a property of the harness, not of the page, so the
  // step that can poison the others runs last and reports whether it did.
  const popouts = [];
  for (const which of ['noopener', 'plain']) {
    trace(`${variant.id}: step F — popout ${which}`);
    await page.bringToFront();
    const before = context.pages().length;
    const knownBefore = new Set(context.pages());
    await scope.locator(`#popout-${which}`).click();
    await page.waitForTimeout(1500);
    const opened = context.pages().filter((p) => !knownBefore.has(p));
    const entry = {
      link: which === 'noopener' ? '<a target="_blank" rel="noopener noreferrer">' : '<a target="_blank">',
      pageCountBefore: before,
      pageCountAfter: context.pages().length,
      openedPageCount: opened.length,
      clickRegistered: (await probeState(scope))[which === 'noopener' ? 'popoutNoopenerClicked' : 'popoutPlainClicked'] ?? '0',
    };
    if (opened.length > 0) {
      entry.openedUrls = opened.map((p) => p.url());
      // A failure inside the opened tab is recorded, not thrown: it is one row of the matrix, and
      // losing the other five variants to it would cost more than the row is worth.
      try {
        entry.inOpenedTab = await probePopup(opened[0], sink, `${variant.id}-popup-${which}`);
      } catch (error) {
        entry.inOpenedTab = null;
        entry.openedTabProbeError = String(error).split('\n')[0];
      }
      if (!keepOpen) for (const p of opened) await p.close();
    }
    popouts.push(entry);
  }

  // --- 4e: drag an <img> out of the frame ---
  trace(`${variant.id}: step E — drag`);
  await page.bringToFront();
  const dragImgBox = await scope.locator('#dragimg').boundingBox();
  const inFrameZone = await scope.locator('#dropzone-inframe').boundingBox();
  await dragBetween(page, dragImgBox, inFrameZone);
  const afterInFrameDrag = await probeState(scope);

  let dragToHost;
  if (variant.topLevel) {
    dragToHost = {
      applicable: false,
      why: 'this variant is not framed, so there is no host document to drag onto; its role here is the in-frame control',
    };
  } else {
    await page.evaluate(() => window.__harness.clearDropLog());
    const hostZone = await page.locator('#hostdrop').boundingBox();
    await dragBetween(page, dragImgBox, hostZone);
    const hostDrops = await page.evaluate(() => window.__harness.dropLog);
    dragToHost = {
      applicable: true,
      hostEventCount: hostDrops.length,
      hostDropCount: hostDrops.filter((d) => d.event === 'drop').length,
      hostEvents: hostDrops,
    };
  }

  // Did the drag leave the page usable? A click that no longer reaches the frame's own handler
  // means Chromium still thinks a drag is in flight, which makes "nothing was dropped on the host"
  // a statement about the harness rather than about the frame boundary. Measured, because the
  // alternative is to guess which of the two the zero means.
  const copyEventsBeforeProbeClick = Number((await probeState(scope)).copyEventCount ?? -1);
  await page.keyboard.press('Escape');
  await scope.locator('#exec-copy').click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(400);
  const copyEventsAfterProbeClick = Number((await probeState(scope)).copyEventCount ?? -1);

  const dragOut = {
    dragStartFiredInFrame: afterInFrameDrag.dragStartFired ?? '0',
    dragStartTypes: afterInFrameDrag.dragStartTypes ?? null,
    controlDropInsideFrameCount: Number(afterInFrameDrag.dropInFrameCount ?? -1),
    controlDropInsideFrameDetail: afterInFrameDrag.dropInFrameDetail ?? null,
    toHost: dragToHost,
    pageStillTakesClicksAfterDrag: copyEventsAfterProbeClick > copyEventsBeforeProbeClick,
    copyEventCountAroundProbeClick: `${copyEventsBeforeProbeClick} -> ${copyEventsAfterProbeClick}`,
    osDragNotMeasured:
      'a drag to the operating system (desktop, another app) leaves the browser entirely and ' +
      'cannot be synthesised or observed from here; this measures only page-to-page.',
  };
  trace(`${variant.id}: done`);

  const consoleLines = sink.drain(variant.id);
  if (!keepOpen) await page.close();

  return {
    id: variant.id,
    role: variant.role,
    why: variant.why,
    frame: {
      topLevel: Boolean(variant.topLevel),
      url,
      crossOriginToHost: !variant.topLevel && variant.origin !== hostOrigin,
      sandboxRequested: variant.sandbox,
      sandboxAttributeOnElement: mounted ? mounted.sandboxAttribute : null,
      allowAttribute: mounted ? mounted.allowAttribute : null,
    },
    context: {
      documentOrigin: atLoad.origin,
      isSecureContext: atLoad.secureContext,
      typeofExecCommand: atLoad.execCommandType,
      queryCommandSupportedCopy: atLoad.queryCommandSupportedCopy,
      permissionsQueryClipboardWrite: atLoad.permClipboardWrite ?? '(not resolved)',
      cssUserSelect: atLoad.userSelect,
      cssWebkitUserSelect: atLoad.webkitUserSelect,
      dragImagePngBytes: Number(atLoad.dragBlobBytes ?? -1),
    },
    execCommandCopyText: execCopy,
    clipboardWriteText: writeText,
    execCommandCopyWithImage: imageCopy,
    manualSelectionCtrlC: manual,
    asyncClipboardImageWrite: asyncImageWrite,
    dragOut,
    popouts,
    consoleDuringVariant: consoleLines,
  };
}

function variantsFor({ frameOrigin, hostOrigin, sandboxBare, sandboxPopups }) {
  return [
    {
      id: 'x-mcpui-plain',
      role: 'subject',
      origin: frameOrigin,
      sandbox: sandboxBare,
      why: 'the string UIResourceRenderer emitted at the bare-mount site, measured this run',
    },
    {
      id: 'x-mcpui-popups',
      role: 'subject',
      origin: frameOrigin,
      sandbox: sandboxPopups,
      why: 'the string it emitted at the span site, measured this run — the only one with allow-popups',
    },
    {
      id: 'x-ctl-top-level',
      role: 'control',
      topLevel: true,
      origin: frameOrigin,
      sandbox: null,
      why: 'not framed at all — proves the gesture, the selection, the drag synthesis and the readers are sound',
    },
    {
      id: 'x-ctl-no-sandbox',
      role: 'control',
      origin: frameOrigin,
      sandbox: null,
      why: 'cross-origin frame, no sandbox attribute — separates the sandbox from the cross-origin permissions-policy default',
    },
    {
      id: 'x-ctl-same-origin',
      role: 'control',
      origin: hostOrigin,
      sandbox: sandboxBare,
      why: 'subject string, same origin as the host — separates cross-origin from the sandbox',
    },
    {
      id: 'x-ctl-allow-clipboard',
      role: 'control',
      origin: frameOrigin,
      sandbox: sandboxBare,
      allow: 'clipboard-write',
      why: 'subject string plus the allow attribute LibreChat does not set',
    },
  ];
}

export async function measureExtraction({
  browser,
  hostOrigin,
  frameOrigin,
  framedUrl,
  sandboxBare,
  sandboxPopups,
  record,
  check,
  keepOpen,
  makeConsoleSink,
}) {
  const token = `t${Date.now().toString(36)}`;

  /** Two postures, and the axis is the READER, not the subject.
   *
   * `reader-granted` grants `clipboard-read`/`clipboard-write` to the HOST origin only, which is
   * what makes the in-browser readback oracle work. The subject frames live on the other origin
   * and get nothing, so the grant cannot be mistaken for the thing that let a frame write.
   * `nothing-granted` grants nothing at all: the in-browser reader is expected to fail there, and
   * whether the stage can still answer depends on `xclip`, which does not go through the browser's
   * permission model. Both are recorded. */
  const postures = [
    { id: 'reader-granted', grants: ['clipboard-read', 'clipboard-write'], grantTo: [hostOrigin] },
    { id: 'nothing-granted', grants: null, grantTo: [] },
  ];

  const out = {};
  for (const posture of postures) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 1000 }, acceptDownloads: true });
    for (const origin of posture.grantTo) {
      if (posture.grants) await context.grantPermissions(posture.grants, { origin });
    }
    const sink = makeConsoleSink();
    const reader = await context.newPage();
    sink.attach(reader, `${posture.id}-reader`);
    await reader.goto(`${hostOrigin}/host.html`);
    await reader.waitForFunction(() => window.__harness && window.__harness.ready === true);

    const readers = await probeReaders(reader);
    trace(
      `posture ${posture.id}: browser reader ${readers.browserReader ? 'live' : 'OFF'}, xclip ${XCLIP_AVAILABLE ? 'available' : 'unavailable'}`,
    );
    const variants = [];
    for (const variant of variantsFor({ frameOrigin, hostOrigin, sandboxBare, sandboxPopups })) {
      variants.push(
        await runVariant({ context, reader, readers, variant, framedUrl, hostOrigin, token, sink, keepOpen }),
      );
    }

    out[posture.id] = {
      grantPermissions: posture.grants
        ? `context.grantPermissions(${JSON.stringify(posture.grants)}) for ${posture.grantTo.join(', ')} ONLY — the reader's origin, never the framed document's`
        : 'nothing granted and nothing denied — the posture a real operator browser starts in',
      readers: {
        'top-level page at the host origin, navigator.clipboard.readText() + read()': readers.browserReaderNote,
        xclip: XCLIP_AVAILABLE
          ? `xclip -selection clipboard -o / -t TARGETS -o, outside the browser process (seed: ${readers.xclipSeedNote})`
          : '(unavailable: no DISPLAY, so this run has only the in-browser reader)',
      },
      readersMeasured: readers,
      readerConsole: sink.drain(`${posture.id}-reader`),
      variants,
    };
    if (!keepOpen) await context.close();
  }

  record('extraction', out);

  const pick = (posture, id) => out[posture].variants.find((v) => v.id === id);
  const SUBJECTS = ['x-mcpui-plain', 'x-mcpui-popups'];
  const STEPS = [
    ['execCommandCopyText', "document.execCommand('copy') over a script selection"],
    ['clipboardWriteText', 'navigator.clipboard.writeText'],
    ['execCommandCopyWithImage', "document.execCommand('copy') over a selection containing an <img>"],
    ['manualSelectionCtrlC', 'mouse drag-select then Ctrl+C'],
    ['asyncClipboardImageWrite', 'navigator.clipboard.write with an image/png (stage 3 repeated, as the flavour-reader control)'],
  ];

  /** A posture carries evidence about the FRAME only where the same act, done UNFRAMED, moved the
   * clipboard: otherwise the subject's failure is unattributable and the posture is named as
   * unable to answer rather than counted as agreement. */
  const postureVerdicts = postures.map(({ id }) => {
    const top = pick(id, 'x-ctl-top-level');
    return {
      posture: id,
      readers: out[id].readersMeasured,
      readerErrors: {
        browserReadText: top.execCommandCopyText.browserReadTextError,
        browserRead: top.execCommandCopyText.browserReadError,
      },
      seedVerifiedBy: top.execCommandCopyText.seedVerifiedBy,
      topLevel: Object.fromEntries(
        STEPS.map(([key]) => [key, { clipboardChanged: top[key].clipboardChanged, payloadSeenBy: top[key].payloadSeenBy }]),
      ),
      topLevelDragStart: top.dragOut.dragStartFiredInFrame,
      topLevelInFrameDrop: top.dragOut.controlDropInsideFrameCount,
      // A posture only discriminates where the seed was provably in place AND the unframed page
      // could displace it.
      discriminates:
        top.execCommandCopyText.seedVerifiedBy.length > 0 && top.execCommandCopyText.clipboardChanged === true,
      dragDiscriminates: top.dragOut.controlDropInsideFrameCount >= 1,
    };
  });

  const answering = postureVerdicts.filter((p) => p.discriminates);
  const dragAnswering = postureVerdicts.filter((p) => p.dragDiscriminates);

  /** Did an `image/png` flavour reach the clipboard, according to EITHER reader? */
  const hasImagePngFlavour = (step) =>
    (step.xclipTargets ?? []).includes('image/png') ||
    (step.browserItemTypes ?? []).some((types) => types.includes('image/png'));

  const verdicts = {
    postures: postureVerdicts,
    clipboard: answering.length
      ? {
          answeredIn: answering.map((p) => p.posture),
          unableToAnswerIn: postureVerdicts
            .filter((p) => !p.discriminates)
            .map((p) => ({
              posture: p.posture,
              because:
                p.seedVerifiedBy.length === 0
                  ? `no reader could verify the seeded sentinel (${JSON.stringify(p.readerErrors)})`
                  : 'the unframed control could not move the clipboard either, so a framed refusal is unattributable',
            })),
          perStep: STEPS.map(([key, label]) => ({
            step: label,
            subjects: SUBJECTS.map((id) => ({
              id,
              sandbox: pick(answering[0].posture, id).frame.sandboxAttributeOnElement,
              clipboardChanged: pick(answering[0].posture, id)[key].clipboardChanged,
              payloadSeenBy: pick(answering[0].posture, id)[key].payloadSeenBy,
              stillHoldsSentinel: pick(answering[0].posture, id)[key].stillHoldsSentinel,
              inFrame: pick(answering[0].posture, id)[key].inFrame,
              flavoursArrived: pick(answering[0].posture, id)[key].xclipTargets,
              browserItemTypes: pick(answering[0].posture, id)[key].browserItemTypes,
            })),
            controls: ['x-ctl-top-level', 'x-ctl-no-sandbox', 'x-ctl-same-origin', 'x-ctl-allow-clipboard'].map((id) => ({
              id,
              clipboardChanged: pick(answering[0].posture, id)[key].clipboardChanged,
              flavoursArrived: pick(answering[0].posture, id)[key].xclipTargets,
            })),
          })),
        }
      : { answeredIn: [], note: 'no posture discriminated; see postures[] for which reader failed' },
    dragOut: dragAnswering.length
      ? {
          answeredIn: dragAnswering.map((p) => p.posture),
          subjects: SUBJECTS.map((id) => ({
            id,
            dragStartFired: pick(dragAnswering[0].posture, id).dragOut.dragStartFiredInFrame,
            controlDropInsideFrame: pick(dragAnswering[0].posture, id).dragOut.controlDropInsideFrameCount,
            hostDropCount: pick(dragAnswering[0].posture, id).dragOut.toHost.hostDropCount,
            pageStillTakesClicksAfterDrag: pick(dragAnswering[0].posture, id).dragOut.pageStillTakesClicksAfterDrag,
            hostEvents: pick(dragAnswering[0].posture, id).dragOut.toHost.hostEvents,
          })),
          controls: ['x-ctl-no-sandbox', 'x-ctl-same-origin'].map((id) => ({
            id,
            controlDropInsideFrame: pick(dragAnswering[0].posture, id).dragOut.controlDropInsideFrameCount,
            hostDropCount: pick(dragAnswering[0].posture, id).dragOut.toHost.hostDropCount,
            pageStillTakesClicksAfterDrag: pick(dragAnswering[0].posture, id).dragOut.pageStillTakesClicksAfterDrag,
            types: pick(dragAnswering[0].posture, id)
              .dragOut.toHost.hostEvents.filter((e) => e.event === 'drop')
              .map((e) => e.types),
          })),
          // The cross-origin row is NOT a verdict about the browser, and saying so is the point.
          crossOriginAttribution: (() => {
            const posture = dragAnswering[0].posture;
            const sameOrigin = pick(posture, 'x-ctl-same-origin').dragOut;
            const subject = pick(posture, SUBJECTS[0]).dragOut;
            const subjectSurvivedItsOwnDrag = subject.pageStillTakesClicksAfterDrag === true;
            return {
              verdict: subjectSurvivedItsOwnDrag ? 'attributable to the frame boundary' : 'NOT ANSWERED',
              why: subjectSurvivedItsOwnDrag
                ? 'the cross-origin drag delivered nothing to the host and the page kept taking clicks afterwards, so the drag ran to completion and the boundary is what stopped it'
                : 'the cross-origin drag delivered nothing AND the page stopped taking synthesised clicks afterwards, which is what an unfinished drag looks like. A cross-origin iframe is also a separate renderer process, and this harness dispatches a drag into one renderer. So the zero cannot be told apart from "the synthesis never crossed the process boundary", and it is reported as an open question rather than as a refusal.',
              sameOriginControlHostDrops: sameOrigin.toHost.hostDropCount,
              sameOriginControlStillTakesClicks: sameOrigin.pageStillTakesClicksAfterDrag,
              sameOriginControlSandbox: pick(posture, 'x-ctl-same-origin').frame.sandboxAttributeOnElement,
              readsAs:
                'the same-origin frame carries the SAME sandbox string and its drop DID reach the host, so whatever stops the cross-origin one, it is not the sandbox attribute.',
            };
          })(),
        }
      : { answeredIn: [], note: 'the in-frame control drop never landed, so no drag was synthesised and nothing here counts' },
    popout: postureVerdicts.map(({ posture }) => ({
      posture,
      rows: [...SUBJECTS, 'x-ctl-top-level', 'x-ctl-no-sandbox', 'x-ctl-same-origin'].map((id) => ({
        id,
        sandbox: pick(posture, id).frame.sandboxAttributeOnElement,
        links: pick(posture, id).popouts.map((p) => ({
          link: p.link,
          openedPageCount: p.openedPageCount,
          clickRegistered: p.clickRegistered,
          inOpenedTab: p.inOpenedTab
            ? {
                origin: p.inOpenedTab.meta.origin,
                openerIsNull: p.inOpenedTab.meta.openerIsNull,
                clipboardWriteImagePng: p.inOpenedTab.clipboardWriteImagePng.result,
                clipboardWriteImagePngError: p.inOpenedTab.clipboardWriteImagePng.errorMessage,
                clipboardWriteText: p.inOpenedTab.clipboardWriteText.result,
                clipboardWriteTextError: p.inOpenedTab.clipboardWriteText.errorMessage,
                downloadFired: p.inOpenedTab.download.fired,
                // Corroboration, not the assertion: Chromium's own explanation of the refusal,
                // which names the sandbox on a document that is top-level. The check stays on the
                // measured download boolean, because a log string is upstream's to reword.
                consoleInOpenedTab: p.inOpenedTab.consoleInPopup,
              }
            : null,
        })),
      })),
    })),
  };

  // One line per question, built from the numbers above rather than typed alongside them, so the
  // summary cannot drift away from the matrix it summarises.
  if (answering.length) {
    const p = answering[0].posture;
    const s = (id, key) => pick(p, id)[key];
    const bothSubjects = (key, fn) => SUBJECTS.map((id) => `${pick(p, id).frame.sandboxAttributeOnElement}: ${fn(s(id, key))}`);
    verdicts.answers = {
      readFrom: `posture ${p}, readers: ${answering[0].seedVerifiedBy.join(' + ')}`,
      "4a execCommand('copy') on a script selection": bothSubjects('execCommandCopyText', (v) =>
        `returned ${v.inFrame.execCommandReturn}, copy event fired ${v.inFrame.copyEventFired}, clipboard CHANGED=${v.clipboardChanged} (seen by ${v.payloadSeenBy.join('+') || 'nobody'}), flavours ${(v.xclipTargets ?? []).filter((t) => t.includes('/')).join(' ')}`),
      '4b clipboard.writeText': bothSubjects('clipboardWriteText', (v) =>
        `${v.inFrame.result} ${v.inFrame.errorName} — "${v.inFrame.errorMessage}"; clipboard CHANGED=${v.clipboardChanged}, still holds the sentinel=${v.stillHoldsSentinel}`),
      "4c execCommand('copy') on a selection containing an <img>": bothSubjects('execCommandCopyWithImage', (v) =>
        `returned ${v.inFrame.execCommandReturn}, selection contained the img=${v.inFrame.selectionContainsImg}, clipboard CHANGED=${v.clipboardChanged}, flavours ${(v.xclipTargets ?? []).filter((t) => t.includes('/')).join(' ')}, image/png present=${hasImagePngFlavour(v)}, the text/html carries ${v.xclipHtmlImgTagCount} <img> tag(s) with src scheme(s) ${JSON.stringify(v.xclipHtmlImgSrcSchemes)}`),
      '4d mouse drag-select then Ctrl+C': bothSubjects('manualSelectionCtrlC', (v) =>
        `selection "${v.inFrame.selectionAfterMouseDrag}" (${v.inFrame.selectionLength} chars), clipboard CHANGED=${v.clipboardChanged}; computed user-select on the copied block=${v.inFrame.userSelectComputed}, on body=${v.inFrame.bodyUserSelectComputed}`),
      '4e drag an <img> onto the host document': SUBJECTS.map((id) => {
        const d = pick(p, id).dragOut;
        return `${pick(p, id).frame.sandboxAttributeOnElement}: dragstart fired=${d.dragStartFiredInFrame}, in-frame drop=${d.controlDropInsideFrameCount}, host drops=${d.toHost.hostDropCount}, page still took clicks after=${d.pageStillTakesClicksAfterDrag}`;
      }),
      '4f target="_blank"': verdicts.popout[0].rows.map(
        (row) =>
          `${row.id} (${row.sandbox ?? 'no sandbox / top-level'}): ${row.links
            .map((l) => `${l.link} -> ${l.openedPageCount} tab(s), click reached the anchor=${l.clickRegistered}${l.inOpenedTab ? `, in it: download fired=${l.inOpenedTab.downloadFired}, clipboard.write(image/png)=${l.inOpenedTab.clipboardWriteImagePng}, writeText=${l.inOpenedTab.clipboardWriteText}, opener null=${l.inOpenedTab.openerIsNull}, origin ${l.inOpenedTab.origin}, console ${JSON.stringify(l.inOpenedTab.consoleInOpenedTab)}` : ''}`)
            .join(' | ')}`,
      ),
    };
  }

  record('extraction-verdicts', verdicts);

  check(
    'extraction: at least one posture discriminates (a seeded sentinel was verified and the unframed page displaced it)',
    answering.length > 0,
    JSON.stringify(postureVerdicts.map((p) => ({ posture: p.posture, seed: p.seedVerifiedBy, moved: p.topLevel }))),
  );
  check(
    'extraction: at least one posture synthesised a real drag (the in-frame control drop landed)',
    dragAnswering.length > 0,
    JSON.stringify(postureVerdicts.map((p) => ({ posture: p.posture, inFrameDrop: p.topLevelInFrameDrop }))),
  );

  for (const { posture } of postureVerdicts) {
    for (const id of [...SUBJECTS, 'x-ctl-top-level']) {
      const v = pick(posture, id);
      check(
        `${posture}/${id}: the document is a secure context and execCommand exists, so a refusal is not the scheme or a missing API`,
        v.context.isSecureContext === 'true' && v.context.typeofExecCommand === 'function',
        `${v.context.isSecureContext} / ${v.context.typeofExecCommand}`,
      );
      check(
        `${posture}/${id}: no CSS obstructs a drag-select (user-select is not "none")`,
        v.context.cssUserSelect !== 'none' && v.context.cssWebkitUserSelect !== 'none',
        `${v.context.cssUserSelect} / ${v.context.cssWebkitUserSelect}`,
      );
      for (const [key] of STEPS) {
        check(
          `${posture}/${id}/${key}: the outcome is a recorded clipboard value, not an absent exception`,
          typeof v[key].clipboardChanged === 'boolean' && Array.isArray(v[key].seedVerifiedBy),
          JSON.stringify({ changed: v[key].clipboardChanged, seed: v[key].seedVerifiedBy }),
        );
      }
    }
  }

  // --- what the numbers say, asserted -------------------------------------------------------
  //
  // Only in postures where the seeded sentinel was verified and the unframed control displaced it.
  // Elsewhere a framed refusal is unattributable and asserting on it would manufacture agreement.
  /** A second, narrower gate. Three of the lines below rest on the ASYNC clipboard API working
   * somewhere unframed — the `image/png` flavour has to be reachable before its absence means
   * anything, and the `allow="clipboard-write"` control is what turns "writeText was refused" into
   * "refused for want of the allow attribute" rather than "refused everywhere in this browser".
   * A headless Chromium refuses the async API to an ungranted top-level page, so that posture can
   * hold the first gate and not this one, and it is named rather than asserted against. */
  const asyncClipboardAnswering = answering.filter(({ posture }) => {
    const top = pick(posture, 'x-ctl-top-level').asyncClipboardImageWrite;
    return top.inFrame.result === 'resolved' && hasImagePngFlavour(top);
  });
  verdicts.imageFlavour = {
    answeredIn: asyncClipboardAnswering.map((p) => p.posture),
    unableToAnswerIn: answering
      .filter((p) => !asyncClipboardAnswering.includes(p))
      .map((p) => ({
        posture: p.posture,
        because: `the unframed control could not put an image/png on the clipboard either (${pick(p.posture, 'x-ctl-top-level').asyncClipboardImageWrite.inFrame.errorMessage}), so "no image/png arrived from the frame" is not attributable here`,
      })),
  };

  for (const { posture } of answering) {
    for (const id of SUBJECTS) {
      const v = pick(posture, id);

      check(
        `${posture}/${id}: execCommand('copy') over a script selection DOES reach the clipboard from the sandboxed cross-origin frame`,
        v.execCommandCopyText.clipboardChanged === true &&
          v.execCommandCopyText.inFrame.execCommandReturn === 'true',
        JSON.stringify(v.execCommandCopyText.payloadSeenBy),
      );
      check(
        `${posture}/${id}: control — clipboard.writeText in the SAME frame is refused and the clipboard keeps the sentinel, so the reader is not simply reporting "changed" every time`,
        v.clipboardWriteText.clipboardChanged === false &&
          v.clipboardWriteText.stillHoldsSentinel === true &&
          v.clipboardWriteText.inFrame.result === 'rejected' &&
          v.clipboardWriteText.inFrame.errorName === 'NotAllowedError',
        JSON.stringify(v.clipboardWriteText.inFrame),
      );
      check(
        `${posture}/${id}: a mouse drag-select plus Ctrl+C — the zero-code path — DOES reach the clipboard, and the selection it copied was not empty`,
        v.manualSelectionCtrlC.clipboardChanged === true &&
          v.manualSelectionCtrlC.inFrame.selectionLength > 0,
        JSON.stringify({
          changed: v.manualSelectionCtrlC.clipboardChanged,
          selection: v.manualSelectionCtrlC.inFrame.selectionAfterMouseDrag,
        }),
      );
      check(
        `${posture}/${id}: copying a selection that CONTAINS an <img> does reach the clipboard, with the image inside the selected range`,
        v.execCommandCopyWithImage.clipboardChanged === true &&
          v.execCommandCopyWithImage.inFrame.selectionContainsImg === 'true',
        JSON.stringify(v.execCommandCopyWithImage.xclipTargets),
      );
    }
  }

  for (const { posture } of asyncClipboardAnswering) {
    const top = pick(posture, 'x-ctl-top-level');
    // The absence of an image flavour only counts if the readers can see that flavour when it IS
    // present, and if the same absence shows up unframed — otherwise it is a fact about Chromium's
    // copy, wrongly filed as a fact about the frame.
    check(
      `${posture}: control — the readers DO see an image/png flavour when one is written (unframed clipboard.write resolved)`,
      top.asyncClipboardImageWrite.inFrame.result === 'resolved' && hasImagePngFlavour(top.asyncClipboardImageWrite),
      JSON.stringify({ result: top.asyncClipboardImageWrite.inFrame.result, targets: top.asyncClipboardImageWrite.xclipTargets }),
    );
    check(
      `${posture}: control — the UNFRAMED page's image-in-selection copy also arrives without image/png, so the missing flavour is Chromium's copy and not the frame`,
      top.execCommandCopyWithImage.clipboardChanged === true && !hasImagePngFlavour(top.execCommandCopyWithImage),
      JSON.stringify(top.execCommandCopyWithImage.xclipTargets),
    );
    for (const id of SUBJECTS) {
      check(
        `${posture}/${id}: the image-in-selection copy delivers markup only — no image/png flavour reached the clipboard`,
        !hasImagePngFlavour(pick(posture, id).execCommandCopyWithImage),
        JSON.stringify(pick(posture, id).execCommandCopyWithImage.xclipTargets),
      );
    }
    check(
      `${posture}: control — the same sandbox string with allow="clipboard-write" DOES let writeText through, so the subjects' refusal is the missing allow attribute and not a browser-wide refusal`,
      pick(posture, 'x-ctl-allow-clipboard').clipboardWriteText.clipboardChanged === true,
      JSON.stringify(pick(posture, 'x-ctl-allow-clipboard').clipboardWriteText.inFrame),
    );
  }

  // The popout and drag rows do not depend on which clipboard reader is live, so they are asserted
  // in every posture.
  for (const { posture } of postureVerdicts) {
    const popupsSubject = pick(posture, 'x-mcpui-popups');
    const plainSubject = pick(posture, 'x-mcpui-plain');

    for (const entry of plainSubject.popouts) {
      check(
        `${posture}/x-mcpui-plain: ${entry.link} opens NOTHING at "allow-scripts allow-same-origin", and the click DID reach the anchor — so the refusal is silent`,
        entry.openedPageCount === 0 && entry.clickRegistered === '1',
        JSON.stringify({ opened: entry.openedPageCount, clickRegistered: entry.clickRegistered }),
      );
    }
    for (const entry of popupsSubject.popouts) {
      check(
        `${posture}/x-mcpui-popups: ${entry.link} DOES open a tab once allow-popups is granted (the negative control above is a refusal, not a broken link)`,
        entry.openedPageCount === 1,
        JSON.stringify({ opened: entry.openedPageCount, clickRegistered: entry.clickRegistered }),
      );
      check(
        `${posture}/x-mcpui-popups: the opened tab CANNOT download, so it inherited a sandbox that never granted allow-downloads`,
        entry.inOpenedTab !== null &&
          entry.inOpenedTab !== undefined &&
          entry.inOpenedTab.download.fired === false &&
          entry.inOpenedTab.download.anchorClicked === '1',
        JSON.stringify(entry.inOpenedTab ? entry.inOpenedTab.download : entry.openedTabProbeError),
      );
    }
    for (const entry of pick(posture, 'x-ctl-no-sandbox').popouts) {
      check(
        `${posture}: control — a tab opened from an UNSANDBOXED cross-origin frame CAN download, so "the opened tab cannot download" is inheritance and not Chromium's own rule for popups`,
        entry.openedPageCount === 1 && entry.inOpenedTab !== null && entry.inOpenedTab.download.fired === true,
        JSON.stringify(entry.inOpenedTab ? entry.inOpenedTab.download : entry.openedTabProbeError),
      );
    }

    for (const id of SUBJECTS) {
      const drag = pick(posture, id).dragOut;
      check(
        `${posture}/${id}: control — the drag WAS synthesised (dragstart fired and the in-frame drop landed), so the host-side count means something`,
        drag.dragStartFiredInFrame === '1' && drag.controlDropInsideFrameCount >= 1,
        JSON.stringify({ dragStart: drag.dragStartFiredInFrame, inFrame: drag.controlDropInsideFrameCount }),
      );
      check(
        `${posture}/${id}: the host-side drop count is a recorded number and the run states whether it is attributable`,
        typeof drag.toHost.hostDropCount === 'number' && typeof drag.pageStillTakesClicksAfterDrag === 'boolean',
        JSON.stringify({ hostDrops: drag.toHost.hostDropCount, stillTakesClicks: drag.pageStillTakesClicksAfterDrag }),
      );
    }
    check(
      `${posture}: control — a SAME-ORIGIN frame carrying the subject sandbox string DOES land a drop on the host, so the sandbox attribute is not what stops a cross-frame drag`,
      pick(posture, 'x-ctl-same-origin').dragOut.toHost.hostDropCount >= 1,
      JSON.stringify(pick(posture, 'x-ctl-same-origin').dragOut.toHost.hostDropCount),
    );
  }

  return { matrix: out, verdicts, postureVerdicts, answering, dragAnswering, xclipAvailable: XCLIP_AVAILABLE };
}

export { XCLIP_AVAILABLE };
