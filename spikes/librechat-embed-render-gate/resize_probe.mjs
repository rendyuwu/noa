/**
 * Standalone measurement of three things about a NOA page rendered inside an mcp-ui iframe, at
 * the `@mcp-ui/client` version LibreChat ships at pin 45cc53c4.
 *
 * It is standalone on purpose. `browser_probe.mjs` needs Postgres, Mongo, a built LibreChat and a
 * model endpoint; these three questions need none of that, because the thing that answers them is
 * the shipped `@mcp-ui/client` bundle plus Chromium's own frame policy. Running the real rig to
 * ask them would make the answers hostage to four services that have nothing to do with the
 * question. The same three checks are also wired into `browser_probe.mjs` so the next full
 * re-verification records them against the live app.
 *
 *   1. iframe height. The package writes `height: 100%` inline and then overwrites it from a
 *      `ui-size-change` message, unclamped. What is the height before any message, and what does
 *      the host do with a number the framed document chooses?
 *   2. clipboard write of an `image/png` from inside the frame, at the exact sandbox strings the
 *      package emits, with no `allow` attribute — which is what LibreChat renders.
 *   3. blob-URL download with the `download` attribute, same two strings.
 *   4. the six remaining ways bytes might leave the frame — the legacy `execCommand('copy')` path,
 *      `clipboard.writeText`, a copy of a selection containing an image, the operator's own
 *      drag-select plus Ctrl+C, an image dragged onto the host document, and `target="_blank"`.
 *      That stage lives in `extraction_probe.mjs`; the same discipline applies, and there the
 *      clipboard is SEEDED with a sentinel and READ BACK from a permitted context, because
 *      `execCommand('copy')` returning `true` is not the claim "the clipboard changed".
 *
 * Discipline, matching the repo's rules on this: every answer is a recorded value — a pixel box,
 * a resolve/reject with the browser's own message — never the mere absence of a thrown exception.
 * Every positive claim ships a control that would fail if the mechanism under test were removed:
 * the resize claim is paired with a resize-disabled mount that must NOT move while still being
 * shown to have received the message; the clipboard and download claims are paired with a
 * top-level page, an unsandboxed frame, a same-origin frame, and frames granted the one token or
 * the one `allow` value the real render sites lack.
 *
 *   node resize_probe.mjs [--headed] [--keep-open]
 *
 * Needs `playwright`, `react`, `react-dom`, `@mcp-ui/client` and `esbuild` resolvable from this
 * directory (see README, "Standalone surface probe").
 */

import { chromium } from 'playwright';
import { XCLIP_AVAILABLE, measureExtraction } from './extraction_probe.mjs';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const EVIDENCE = join(HERE, 'evidence');
const RUNTIME = join(HERE, '.runtime');
const BUNDLE = join(RUNTIME, 'probe_bundle.js');

const PORT = Number(process.env.PROBE_PORT ?? 4599);
/** Two hostnames on one listener. Both are potentially-trustworthy origins, so a failure is
 * attributable to the frame policy and not to the scheme; and they are different origins, which
 * is the arrangement the live rig has (the frame is on NOA's host, the chat page is not). */
const HOST_ORIGIN = `http://localhost:${PORT}`;
const FRAME_ORIGIN = `http://127.0.0.1:${PORT}`;

const HEADED = process.argv.includes('--headed');
const KEEP_OPEN = process.argv.includes('--keep-open');

/** Headed and headless get separate artifacts because they gave different answers to the
 * clipboard question, and collapsing them into one file would lose the one that matters. */
const ARTIFACT = join(
  EVIDENCE,
  HEADED ? 'resize-clipboard-download-probe-headed.json' : 'resize-clipboard-download-probe.json',
);

/** The height the framed document asks for in measurement 1(b), and the absurd one in 1(d). */
const REQUESTED_HEIGHT = 640;
const ABSURD_HEIGHT = 20000;

const require = createRequire(import.meta.url);

const results = {
  startedAt: new Date().toISOString(),
  environment: {},
  stages: {},
  failures: [],
};

function record(stage, value) {
  results.stages[stage] = value;
  console.log(`--- ${stage}\n${JSON.stringify(value, null, 2)}`);
}

function check(name, condition, detail) {
  if (condition) {
    console.log(`ok    ${name}`);
  } else {
    console.log(`FAIL  ${name}: ${detail ?? ''}`);
    results.failures.push({ name, detail: detail ?? null });
  }
}

/** Version of an installed package, found by walking up from its resolved entry point.
 *
 * `require('<name>/package.json')` is not usable across the board: `@mcp-ui/client` publishes an
 * `exports` map with only `.` and `./ui-resource-renderer.wc.js`, so the subpath throws. Recording
 * the resolved path next to the version matters more than the version alone — the whole claim is
 * that these are the bytes LibreChat ships. */
function pkgInfo(name) {
  try {
    const entry = require.resolve(name);
    let dir = dirname(entry);
    for (let depth = 0; depth < 8; depth += 1) {
      const candidate = join(dir, 'package.json');
      if (existsSync(candidate)) {
        const json = JSON.parse(readFileSync(candidate, 'utf8'));
        if (json.name === name) return { version: json.version, resolvedFrom: entry };
      }
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
    return { version: '(package.json not found)', resolvedFrom: entry };
  } catch (error) {
    return { version: `(resolve failed: ${error && error.code})`, resolvedFrom: null };
  }
}

/** React + the real `@mcp-ui/client` into one browser bundle. React 18 ships CJS only, so a
 * bundler is not avoidable here; an import map would resolve the bare specifiers and then fail on
 * `require`. */
async function buildBundle() {
  const esbuild = await import('esbuild');
  const result = await esbuild.build({
    absWorkingDir: HERE,
    entryPoints: [join(HERE, 'probe_pages', 'harness_entry.js')],
    outfile: BUNDLE,
    bundle: true,
    format: 'iife',
    platform: 'browser',
    target: 'chrome120',
    define: { 'process.env.NODE_ENV': '"production"' },
    logLevel: 'warning',
    metafile: false,
  });
  return {
    esbuildVersion: esbuild.version ?? pkgInfo('esbuild').version,
    warnings: result.warnings.map((w) => w.text),
    bytes: readFileSync(BUNDLE).byteLength,
  };
}

function startServer() {
  const files = new Map([
    ['/host.html', [join(HERE, 'probe_pages', 'host.html'), 'text/html; charset=utf-8']],
    ['/framed.html', [join(HERE, 'probe_pages', 'framed.html'), 'text/html; charset=utf-8']],
    ['/probe_bundle.js', [BUNDLE, 'text/javascript; charset=utf-8']],
  ]);

  const server = createServer((req, res) => {
    const path = new URL(req.url ?? '/', 'http://placeholder').pathname;
    const entry = files.get(path);
    if (!entry) {
      res.writeHead(404, { 'content-type': 'text/plain' });
      res.end('not found');
      return;
    }
    res.writeHead(200, { 'content-type': entry[1], 'cache-control': 'no-store' });
    res.end(readFileSync(entry[0]));
  });

  // The readiness gate is the socket, one layer below anything under test: if the harness cannot
  // listen, this rejects with that fact rather than turning into a timeout somewhere in the page.
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(PORT, '0.0.0.0', () => resolve(server));
  });
}

function framedUrl(origin, slot) {
  return `${origin}/framed.html?slot=${encodeURIComponent(slot)}`;
}

/** Post a `ui-size-change` from INSIDE the frame, which is the only source the package accepts
 * (`dist/index.mjs` gates the handler on `event.source === iframe.contentWindow`). A second,
 * differently-typed message rides along so the host's sender-agnostic log can prove the channel
 * carried traffic even in the run where the height must not move. */
async function postSizeChange(page, slot, payload) {
  const frame = page.frames().find((f) => f.url().includes(`slot=${encodeURIComponent(slot)}`));
  if (!frame) throw new Error(`no frame found for slot ${slot}`);
  await frame.evaluate((p) => {
    window.parent.postMessage({ type: 'ui-size-change', payload: p }, '*');
    window.parent.postMessage({ type: 'noa-probe-size-posted', payload: p }, '*');
  }, payload);
  await page.waitForTimeout(400);
}

const measure = (page, slot) => page.evaluate((s) => window.__harness.measure(s), slot);

const sizePostReceipts = (page, slot) =>
  page.evaluate(
    (s) =>
      window.__harness.received.filter(
        (m) => m.data && m.data.type === 'noa-probe-size-posted' && m.data.payload.slot === s,
      ).length,
    slot,
  );

// --- Measurement 1 -------------------------------------------------------------------------

const RESIZE_SLOTS = [
  {
    slot: 'span-autoresize',
    wrapper: 'mcpui-resource-span',
    autoResizeIframe: { width: true, height: true },
    sandboxPermissions: 'allow-popups',
    site: 'MCPUIResource.tsx:39-47 — auto-height inline-block span wrapper, allow-popups granted',
  },
  {
    slot: 'bare-autoresize',
    wrapper: 'toolcall-bare',
    autoResizeIframe: { width: true, height: true },
    site: 'ToolCallInfo.tsx:167-173 — no wrapper, no sandboxPermissions',
  },
  {
    slot: 'span-no-autoresize',
    wrapper: 'mcpui-resource-span',
    site: 'negative control — autoResizeIframe absent, same wrapper as the span site',
  },
  {
    slot: 'span-autoresize-absurd',
    wrapper: 'mcpui-resource-span',
    autoResizeIframe: { width: true, height: true },
    sandboxPermissions: 'allow-popups',
    site: 'the span site again, used for the unclamped-height question',
  },
];

async function measureResize(page) {
  await page.goto(`${HOST_ORIGIN}/host.html`);
  await page.waitForFunction(() => window.__harness && window.__harness.ready === true);

  for (const spec of RESIZE_SLOTS) {
    await page.evaluate(
      (s) => window.__harness.mount(s),
      { ...spec, url: framedUrl(FRAME_ORIGIN, spec.slot) },
    );
  }

  // Ready gate is the framed documents announcing themselves, not a fixed sleep.
  await page.waitForFunction(
    (n) =>
      window.__harness.received.filter((m) => m.data && m.data.type === 'noa-probe-frame-ready')
        .length >= n,
    RESIZE_SLOTS.length,
    { timeout: 30_000 },
  );
  await page.waitForTimeout(300);

  const initial = {};
  for (const spec of RESIZE_SLOTS) initial[spec.slot] = await measure(page, spec.slot);

  // Control on the OTHER gate: the right message type from the wrong source. The package gates
  // its handler on `event.source === iframe.contentWindow`, so a post from the host's own window
  // must leave every box alone — otherwise "the box moved" would only mean "any message moves
  // it", and the positive measurement below would be worth nothing.
  await page.evaluate(
    (h) => window.postMessage({ type: 'ui-size-change', payload: { height: h } }, '*'),
    REQUESTED_HEIGHT,
  );
  await page.waitForTimeout(400);
  const afterWrongSource = {};
  for (const spec of RESIZE_SLOTS) afterWrongSource[spec.slot] = await measure(page, spec.slot);

  // (b) the height the framed document asks for, at both live sites.
  const afterRequested = {};
  for (const slot of ['span-autoresize', 'bare-autoresize']) {
    await postSizeChange(page, slot, { slot, height: REQUESTED_HEIGHT });
    afterRequested[slot] = await measure(page, slot);
  }

  // (c) the control. Same message, same wrapper, `autoResizeIframe` absent.
  const controlSlot = 'span-no-autoresize';
  await postSizeChange(page, controlSlot, { slot: controlSlot, height: REQUESTED_HEIGHT });
  const control = await measure(page, controlSlot);
  const controlReceipts = await sizePostReceipts(page, controlSlot);

  // (d) is there a host-side clamp?
  const absurdSlot = 'span-autoresize-absurd';
  await postSizeChange(page, absurdSlot, { slot: absurdSlot, height: ABSURD_HEIGHT });
  const absurd = await measure(page, absurdSlot);

  // How much of the initial height is the UA default iframe border that Tailwind's preflight
  // zeroes. Without this the initial number cannot be told apart from a layout result.
  const noPreflightPage = await page.context().newPage();
  await noPreflightPage.goto(`${HOST_ORIGIN}/host.html?preflight=0`);
  await noPreflightPage.waitForFunction(() => window.__harness && window.__harness.ready === true);
  await noPreflightPage.evaluate(
    (s) => window.__harness.mount(s),
    { ...RESIZE_SLOTS[0], slot: 'span-autoresize-no-preflight', url: framedUrl(FRAME_ORIGIN, 'no-preflight') },
  );
  await noPreflightPage.waitForTimeout(1200);
  const withoutPreflight = await measure(noPreflightPage, 'span-autoresize-no-preflight');
  await noPreflightPage.close();

  const stage = {
    note:
      'Heights are getBoundingClientRect().height in CSS px at a 1280x1000 viewport with the ' +
      'message column fixed at 768px, Tailwind v3 preflight reproduced (border-width:0 and ' +
      'iframe{display:block}). The column width is an input, recorded so the numbers reproduce.',
    columnWidthPx: 768,
    rootFontSizePx: await page.evaluate(
      () => window.getComputedStyle(document.documentElement).fontSize,
    ),
    requestedHeightPx: REQUESTED_HEIGHT,
    absurdHeightPx: ABSURD_HEIGHT,
    initial,
    afterSizeChangeFromWrongSource: afterWrongSource,
    afterRequestedHeight: afterRequested,
    control: { measured: control, sizePostReceiptsSeenByHost: controlReceipts },
    absurd,
    withoutPreflight,
  };
  record('resize', stage);

  const spanInitial = initial['span-autoresize'].iframe.rect.height;
  const bareInitial = initial['bare-autoresize'].iframe.rect.height;

  record('resize-verdicts', {
    'initial height, span-wrapper site (MCPUIResource.tsx:39)': `${spanInitial}px at ${initial['span-autoresize'].iframe.rect.width}px wide`,
    'initial height, bare-mount site (ToolCallInfo.tsx:167-173)': `${bareInitial}px at ${initial['bare-autoresize'].iframe.rect.width}px wide`,
    'why that number':
      'the package sets height:100% inline; both containers are auto-height, so the percentage ' +
      'resolves to auto and the box falls back to the UA intrinsic iframe height. Dropping ' +
      "Tailwind's preflight puts the UA's 2px iframe border back and the same box measures " +
      `${withoutPreflight.iframe.rect.height}px.`,
    [`after ui-size-change height:${REQUESTED_HEIGHT}`]: {
      span: `${afterRequested['span-autoresize'].iframe.rect.height}px, inline height ${afterRequested['span-autoresize'].iframe.inlineStyleHeight}`,
      bare: `${afterRequested['bare-autoresize'].iframe.rect.height}px, inline height ${afterRequested['bare-autoresize'].iframe.inlineStyleHeight}`,
      widthWhenPayloadOmitsIt: `inline ${afterRequested['span-autoresize'].iframe.inlineStyleWidth}, measured ${afterRequested['span-autoresize'].iframe.rect.width}px (unchanged)`,
    },
    'negative control, autoResizeIframe absent': `${initial[controlSlot].iframe.rect.height}px -> ${control.iframe.rect.height}px, and the host logged ${controlReceipts} message receipt(s) from that frame, so the unchanged height is a refusal and not a lost message`,
    'negative control, same message from the host window instead of the frame': `every slot stayed at ${afterWrongSource['span-autoresize'].iframe.rect.height}px, so the source gate holds and "the box moved" is not just "any message moves it"`,
    [`after ui-size-change height:${ABSURD_HEIGHT}`]: `${absurd.iframe.rect.height}px, inline height ${absurd.iframe.inlineStyleHeight} — applied verbatim, so there is no host-side clamp`,
  });
  check('span-wrapper site renders a frame with a measurable box', spanInitial > 0, String(spanInitial));
  check('bare-mount site renders a frame with a measurable box', bareInitial > 0, String(bareInitial));

  for (const slot of ['span-autoresize', 'bare-autoresize']) {
    const after = afterRequested[slot];
    check(
      `${slot}: ui-size-change height is applied verbatim (${REQUESTED_HEIGHT}px)`,
      after.iframe.rect.height === REQUESTED_HEIGHT,
      `${after.iframe.rect.height} / inline ${after.iframe.inlineStyleHeight}`,
    );
    check(
      `${slot}: width is untouched when the payload omits it`,
      after.iframe.inlineStyleWidth === '100%' &&
        after.iframe.rect.width === initial[slot].iframe.rect.width,
      `inline ${after.iframe.inlineStyleWidth}, ${initial[slot].iframe.rect.width} -> ${after.iframe.rect.width}`,
    );
    check(
      `${slot}: the initial height and the resized height differ, so the measurement discriminates`,
      initial[slot].iframe.rect.height !== after.iframe.rect.height,
      `${initial[slot].iframe.rect.height} vs ${after.iframe.rect.height}`,
    );
  }

  for (const spec of RESIZE_SLOTS) {
    check(
      `control: ui-size-change from the host's own window does NOT move ${spec.slot} (source gate holds)`,
      afterWrongSource[spec.slot].iframe.rect.height === initial[spec.slot].iframe.rect.height &&
        afterWrongSource[spec.slot].iframe.inlineStyleHeight ===
          initial[spec.slot].iframe.inlineStyleHeight,
      `${initial[spec.slot].iframe.rect.height} -> ${afterWrongSource[spec.slot].iframe.rect.height}`,
    );
  }

  check(
    'control: with autoResizeIframe absent the height does NOT move',
    control.iframe.rect.height === initial[controlSlot].iframe.rect.height &&
      control.iframe.inlineStyleHeight === initial[controlSlot].iframe.inlineStyleHeight,
    `${initial[controlSlot].iframe.rect.height} -> ${control.iframe.rect.height}`,
  );
  check(
    'control: the host DID receive the frame message, so the unchanged height is a refusal',
    controlReceipts >= 1,
    `receipts=${controlReceipts}`,
  );
  check(
    `absurd height ${ABSURD_HEIGHT}px is applied verbatim (no host-side clamp)`,
    absurd.iframe.rect.height === ABSURD_HEIGHT,
    `${absurd.iframe.rect.height} / inline ${absurd.iframe.inlineStyleHeight}`,
  );

  const sandboxBare = initial['bare-autoresize'].sandboxAttribute;
  const sandboxPopups = initial['span-autoresize'].sandboxAttribute;
  record('sandbox-strings', { bareMount: sandboxBare, withAllowPopups: sandboxPopups });
  check(
    'sandbox string without sandboxPermissions is "allow-scripts allow-same-origin"',
    sandboxBare === 'allow-scripts allow-same-origin',
    sandboxBare,
  );
  check(
    'sandbox string with sandboxPermissions:allow-popups adds only allow-popups',
    sandboxPopups === 'allow-popups allow-scripts allow-same-origin',
    sandboxPopups,
  );
  check('neither sandbox string grants allow-downloads', !`${sandboxBare} ${sandboxPopups}`.includes('allow-downloads'));

  return { sandboxBare, sandboxPopups };
}

// --- Measurements 2 and 3 ------------------------------------------------------------------

function surfaceVariants(sandboxBare, sandboxPopups) {
  return [
    {
      id: 'mcpui-plain',
      role: 'subject',
      origin: FRAME_ORIGIN,
      sandbox: sandboxBare,
      allow: null,
      why: 'the string UIResourceRenderer emitted at the bare-mount site, measured this run',
    },
    {
      id: 'mcpui-popups',
      role: 'subject',
      origin: FRAME_ORIGIN,
      sandbox: sandboxPopups,
      allow: null,
      why: 'the string it emitted at the span site, measured this run',
    },
    {
      id: 'ctl-top-level',
      role: 'control',
      topLevel: true,
      origin: FRAME_ORIGIN,
      sandbox: null,
      allow: null,
      why: 'not framed at all — proves the gesture, the blob and the harness are sound',
    },
    {
      id: 'ctl-no-sandbox',
      role: 'control',
      origin: FRAME_ORIGIN,
      sandbox: null,
      allow: null,
      why: 'cross-origin frame, no sandbox attribute — separates the sandbox from the cross-origin permissions-policy default',
    },
    {
      id: 'ctl-allow-clipboard',
      role: 'control',
      origin: FRAME_ORIGIN,
      sandbox: sandboxBare,
      allow: 'clipboard-write',
      why: 'subject string plus the allow attribute LibreChat does not set',
    },
    {
      id: 'ctl-allow-downloads',
      role: 'control',
      origin: FRAME_ORIGIN,
      sandbox: `${sandboxBare} allow-downloads`,
      allow: null,
      why: 'subject string plus the one sandbox token both real strings lack',
    },
    {
      id: 'ctl-same-origin',
      role: 'control',
      origin: HOST_ORIGIN,
      sandbox: sandboxBare,
      allow: null,
      why: 'same origin as the host document — separates cross-origin from the sandbox',
    },
  ];
}

const readProbe = (locator) => locator.evaluate((el) => ({ ...el.dataset }));

async function runVariant(context, variant, consoleSink) {
  const url = framedUrl(variant.origin, variant.id);

  // One page per variant, brought to front before the click: `document.hasFocus()` is an input to
  // the clipboard question, and a background tab would answer a different question than the one
  // asked. The focus state is recorded next to every result either way.
  const page = await context.newPage();
  consoleSink.attach(page, variant.id);

  let mounted = null;
  /** Where `#probe`, `#clip` and `#dl` live for this variant: the page itself, or inside the frame. */
  let scope;

  if (variant.topLevel) {
    await page.goto(url);
    scope = page;
  } else {
    await page.goto(`${HOST_ORIGIN}/host.html`);
    await page.waitForFunction(() => window.__harness && window.__harness.ready === true);
    mounted = await page.evaluate(
      (spec) => window.__harness.mountRaw(spec),
      { slot: variant.id, url, sandbox: variant.sandbox, allow: variant.allow, width: 460, height: 300 },
    );
    scope = page.frameLocator(`iframe[title="raw probe frame ${variant.id}"]`);
  }
  await page.bringToFront();

  // Readiness gate is the framed document's own `data-ready`, which it sets once the PNG blob
  // exists. Gating on anything the clipboard call touches would turn a refusal into a timeout,
  // and a timeout names nothing.
  await scope.locator('#probe[data-ready="1"]').waitFor({ state: 'attached', timeout: 20_000 });
  const probe = scope.locator('#probe');
  const atLoad = await readProbe(probe);

  // --- measurement 2: clipboard write, from a real click ---
  await scope.locator('#clip').click();
  await page.waitForTimeout(1500);
  const afterClip = await readProbe(probe);

  // --- measurement 3: blob-URL download with the download attribute ---
  const waiting = page
    .waitForEvent('download', { timeout: 6000 })
    .then(async (download) => ({
      fired: true,
      suggestedFilename: download.suggestedFilename(),
      failure: await download.failure(),
    }))
    .catch((error) => ({ fired: false, timeoutError: String(error).split('\n')[0] }));
  await scope.locator('#dl').click();
  const downloadOutcome = await waiting;
  await page.waitForTimeout(300);
  const afterDownload = await readProbe(probe);

  const consoleLines = consoleSink.drain(variant.id);
  if (!KEEP_OPEN) await page.close();

  return {
    id: variant.id,
    role: variant.role,
    why: variant.why,
    frame: {
      topLevel: Boolean(variant.topLevel),
      url,
      crossOriginToHost: variant.origin !== HOST_ORIGIN && !variant.topLevel,
      sandboxRequested: variant.sandbox,
      sandboxAttributeOnElement: mounted ? mounted.sandboxAttribute : null,
      hasSandboxAttribute: mounted ? mounted.hasSandboxAttribute : false,
      allowAttribute: mounted ? mounted.allowAttribute : null,
    },
    context: {
      documentOrigin: atLoad.origin,
      isSecureContext: atLoad.secureContext,
      typeofClipboard: atLoad.clipboardApi,
      typeofClipboardItem: atLoad.clipboardItemType,
      clipboardItemSupportsImagePng: atLoad.clipboardItemSupportsPng,
      permissionsQueryClipboardWrite: atLoad.permClipboardWrite ?? '(not resolved)',
      pngBlobBytes: Number(atLoad.pngBlobBytes),
      hasFocusAtLoad: atLoad.hasFocusAtLoad,
    },
    clipboardWrite: {
      result: afterClip.clipResult ?? '(no result recorded)',
      errorName: afterClip.clipErrorName ?? null,
      errorMessage: afterClip.clipError ?? null,
      documentHadFocusAtClick: afterClip.clipHasFocus ?? null,
    },
    download: {
      anchorClicked: afterDownload.dlAnchorClicked ?? '(not clicked)',
      anchorError: afterDownload.dlError ?? null,
      ...downloadOutcome,
    },
    consoleDuringVariant: consoleLines,
  };
}

function makeConsoleSink() {
  const byLabel = new Map();
  return {
    attach(page, label) {
      if (!byLabel.has(label)) byLabel.set(label, []);
      const bucket = byLabel.get(label);
      page.on('console', (msg) => bucket.push(`[${msg.type()}] ${msg.text()}`));
      page.on('pageerror', (err) => bucket.push(`[pageerror] ${String(err)}`));
    },
    drain(label) {
      const bucket = byLabel.get(label) ?? [];
      byLabel.set(label, []);
      return bucket;
    },
  };
}

/** The whole matrix, once per clipboard-permission posture.
 *
 * Playwright can pre-grant clipboard permissions, which would quietly turn "a real operator's
 * browser refuses this" into "the harness was asked nicely". So the default no-grant posture runs
 * first and is the one to read; the granted posture runs beside it to show whether granting is
 * even the axis in play. Both are recorded. */
async function measureSurfaces(browser, sandboxBare, sandboxPopups) {
  const postures = [
    { id: 'default-no-grant', grants: null },
    { id: 'granted-clipboard-read-write', grants: ['clipboard-read', 'clipboard-write'] },
  ];
  const out = {};

  for (const posture of postures) {
    const context = await browser.newContext({
      viewport: { width: 1280, height: 1000 },
      acceptDownloads: true,
    });
    if (posture.grants) await context.grantPermissions(posture.grants, { origin: FRAME_ORIGIN });
    if (posture.grants) await context.grantPermissions(posture.grants, { origin: HOST_ORIGIN });

    const sink = makeConsoleSink();
    const variants = [];
    for (const variant of surfaceVariants(sandboxBare, sandboxPopups)) {
      variants.push(await runVariant(context, variant, sink));
    }
    out[posture.id] = {
      grantPermissions: posture.grants
        ? `context.grantPermissions(${JSON.stringify(posture.grants)}) for both ${HOST_ORIGIN} and ${FRAME_ORIGIN}`
        : 'nothing granted and nothing denied — the posture a real operator browser starts in',
      variants,
    };
    if (!KEEP_OPEN) await context.close();
  }

  const pick = (posture, id) => out[posture].variants.find((v) => v.id === id);
  const SUBJECTS = ['mcpui-plain', 'mcpui-popups'];

  /** A posture only carries evidence about the FRAME if the same call succeeds outside a frame.
   * Where the top-level control also fails, the browser is refusing globally and the subject's
   * failure is unattributable — that posture is named as unable to answer rather than counted as
   * agreement. */
  const postureVerdicts = postures.map(({ id }) => {
    const top = pick(id, 'ctl-top-level');
    return {
      posture: id,
      topLevelClipboard: top.clipboardWrite.result,
      topLevelClipboardError: top.clipboardWrite.errorMessage,
      topLevelDownloadFired: top.download.fired,
      clipboardDiscriminates: top.clipboardWrite.result === 'resolved',
      downloadDiscriminates: top.download.fired === true,
    };
  });

  const clipboardPostures = postureVerdicts.filter((p) => p.clipboardDiscriminates);
  const downloadPostures = postureVerdicts.filter((p) => p.downloadDiscriminates);

  const verdicts = {
    postures: postureVerdicts,
    clipboard: clipboardPostures.length
      ? {
          answeredIn: clipboardPostures.map((p) => p.posture),
          unableToAnswerIn: postureVerdicts
            .filter((p) => !p.clipboardDiscriminates)
            .map((p) => ({ posture: p.posture, because: `top-level control also failed: ${p.topLevelClipboardError}` })),
          subjects: SUBJECTS.map((id) => ({
            id,
            sandbox: pick(clipboardPostures[0].posture, id).frame.sandboxAttributeOnElement,
            result: pick(clipboardPostures[0].posture, id).clipboardWrite.result,
            error: pick(clipboardPostures[0].posture, id).clipboardWrite.errorMessage,
          })),
          isolation: {
            'cross-origin frame, no sandbox at all': pick(clipboardPostures[0].posture, 'ctl-no-sandbox').clipboardWrite.result,
            'subject sandbox, same origin as host': pick(clipboardPostures[0].posture, 'ctl-same-origin').clipboardWrite.result,
            'subject sandbox, cross-origin, allow="clipboard-write"': pick(clipboardPostures[0].posture, 'ctl-allow-clipboard').clipboardWrite.result,
          },
        }
      : { answeredIn: [], note: 'no posture discriminated: the top-level control failed everywhere' },
    download: downloadPostures.length
      ? {
          answeredIn: downloadPostures.map((p) => p.posture),
          subjects: SUBJECTS.map((id) => ({
            id,
            sandbox: pick(downloadPostures[0].posture, id).frame.sandboxAttributeOnElement,
            fired: pick(downloadPostures[0].posture, id).download.fired,
            anchorClicked: pick(downloadPostures[0].posture, id).download.anchorClicked,
            consoleDuringVariant: pick(downloadPostures[0].posture, id).consoleDuringVariant,
          })),
          isolation: {
            'cross-origin frame, no sandbox at all': pick(downloadPostures[0].posture, 'ctl-no-sandbox').download.fired,
            'subject sandbox, same origin as host': pick(downloadPostures[0].posture, 'ctl-same-origin').download.fired,
            'subject sandbox plus allow-downloads': pick(downloadPostures[0].posture, 'ctl-allow-downloads').download.fired,
          },
        }
      : { answeredIn: [], note: 'no posture discriminated: the top-level control failed everywhere' },
  };

  record('surfaces', out);
  record('surface-verdicts', verdicts);

  check(
    'at least one clipboard posture discriminates (top-level control resolved somewhere)',
    clipboardPostures.length > 0,
    JSON.stringify(postureVerdicts),
  );
  check(
    'at least one download posture discriminates (top-level control fired somewhere)',
    downloadPostures.length > 0,
    JSON.stringify(postureVerdicts),
  );

  for (const { posture } of postureVerdicts) {
    for (const id of SUBJECTS) {
      const v = pick(posture, id);
      check(
        `${posture}/${id}: the framed document is a secure context, so a refusal is not the scheme`,
        v.context.isSecureContext === 'true',
        v.context.isSecureContext,
      );
      check(
        `${posture}/${id}: ClipboardItem exists and claims image/png support`,
        v.context.typeofClipboardItem === 'function' && v.context.clipboardItemSupportsImagePng === 'true',
        `${v.context.typeofClipboardItem} / ${v.context.clipboardItemSupportsImagePng}`,
      );
      check(
        `${posture}/${id}: clipboard outcome is a recorded value, not an absent exception`,
        ['resolved', 'rejected', 'clipboarditem-threw'].includes(v.clipboardWrite.result),
        v.clipboardWrite.result,
      );
      check(
        `${posture}/${id}: download outcome is a recorded value, not an absent exception`,
        typeof v.download.fired === 'boolean' && v.download.anchorClicked === '1',
        JSON.stringify(v.download),
      );
    }
  }

  for (const { posture } of clipboardPostures) {
    for (const id of SUBJECTS) {
      check(
        `${posture}/${id}: clipboard write is REFUSED while the top-level control succeeds`,
        pick(posture, id).clipboardWrite.result === 'rejected',
        pick(posture, id).clipboardWrite.errorMessage,
      );
    }
    check(
      `${posture}: control — the same sandbox string succeeds with allow="clipboard-write", so the sandbox is not the cause`,
      pick(posture, 'ctl-allow-clipboard').clipboardWrite.result === 'resolved',
      pick(posture, 'ctl-allow-clipboard').clipboardWrite.errorMessage,
    );
    check(
      `${posture}: control — a cross-origin frame with NO sandbox is refused the same way, so the cause is the cross-origin permissions policy`,
      pick(posture, 'ctl-no-sandbox').clipboardWrite.result === 'rejected',
      pick(posture, 'ctl-no-sandbox').clipboardWrite.errorMessage,
    );
  }

  for (const { posture } of downloadPostures) {
    for (const id of SUBJECTS) {
      check(
        `${posture}/${id}: download does NOT fire while the top-level control does`,
        pick(posture, id).download.fired === false,
        JSON.stringify(pick(posture, id).download),
      );
    }
    check(
      `${posture}: control — the same sandbox string plus allow-downloads DOES fire, so the missing token is the cause`,
      pick(posture, 'ctl-allow-downloads').download.fired === true,
      JSON.stringify(pick(posture, 'ctl-allow-downloads').download),
    );
    check(
      `${posture}: control — same-origin with the subject sandbox still does not fire, so cross-origin is not the cause`,
      pick(posture, 'ctl-same-origin').download.fired === false,
      JSON.stringify(pick(posture, 'ctl-same-origin').download),
    );
  }

  return { matrix: out, verdicts };
}

// --- driver --------------------------------------------------------------------------------

async function main() {
  mkdirSync(EVIDENCE, { recursive: true });
  mkdirSync(RUNTIME, { recursive: true });

  const build = await buildBundle();
  const mcpUi = pkgInfo('@mcp-ui/client');
  const server = await startServer();

  const browser = await chromium.launch({ headless: !HEADED });
  const page = await (await browser.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.log(`[browser console] ${msg.text()}`);
  });

  results.environment = {
    date: new Date().toISOString().slice(0, 10),
    chromiumVersion: browser.version(),
    playwrightVersion: pkgInfo('playwright').version,
    nodeVersion: process.version,
    headless: !HEADED,
    mcpUiClientVersion: mcpUi.version,
    mcpUiClientResolvedFrom: mcpUi.resolvedFrom,
    librechatPin: '45cc53c40b47645b887c3bb996168e06aaa83f4c',
    reactVersion: pkgInfo('react').version,
    bundler: `esbuild ${build.esbuildVersion}`,
    bundleBytes: build.bytes,
    hostOrigin: HOST_ORIGIN,
    frameOrigin: FRAME_ORIGIN,
    // Stage 4's second clipboard reader. It talks to the X server, so it answers in the posture
    // where the browser grants nothing; without a display the stage has only the in-browser one.
    display: process.env.DISPLAY || '(unset)',
    xclipAvailable: XCLIP_AVAILABLE,
  };
  console.log(`--- environment\n${JSON.stringify(results.environment, null, 2)}`);
  check('esbuild produced no warnings', build.warnings.length === 0, build.warnings.join('; '));

  try {
    const { sandboxBare, sandboxPopups } = await measureResize(page);
    await page.screenshot({ path: join(EVIDENCE, 'surface-probe-resize.png'), fullPage: false });
    await measureSurfaces(browser, sandboxBare, sandboxPopups);
    await measureExtraction({
      browser,
      hostOrigin: HOST_ORIGIN,
      frameOrigin: FRAME_ORIGIN,
      framedUrl,
      sandboxBare,
      sandboxPopups,
      record,
      check,
      keepOpen: KEEP_OPEN,
      makeConsoleSink,
    });
  } catch (error) {
    results.failures.push({ name: 'run', detail: String(error) });
    console.log(`FAIL  run: ${error}`);
    await page.screenshot({ path: join(EVIDENCE, 'surface-probe-failure.png') }).catch(() => {});
  } finally {
    results.finishedAt = new Date().toISOString();
    writeFileSync(ARTIFACT, JSON.stringify(results, null, 2));
    console.log(`\nwrote ${ARTIFACT}`);
    if (!KEEP_OPEN) {
      await browser.close();
      server.close();
    }
  }

  if (results.failures.length > 0) {
    console.log(`\n${results.failures.length} failed check(s)`);
    process.exit(1);
  }
  console.log('\nall checks green');
}

await main();
