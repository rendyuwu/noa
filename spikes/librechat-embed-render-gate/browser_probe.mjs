/**
 * E2-E6 — the live run for T59 items (d) and (f), plus three properties of the rendered frame.
 *
 * Drives a real browser against a real LibreChat at pin 45cc53c4, talking to a real NOA
 * process over MCP, and answers one question with a measurement rather than a source read:
 * when NOA returns a `text/uri-list` UI resource, does the frame LibreChat renders sit on
 * NOA's origin with the `noa_session` cookie riding into it, so the decision POST
 * V22 requires is possible from inside it?
 *
 * What each stage proves is written next to it. Two design points worth stating here:
 *
 * - **The negative control is not optional.** `embed_probe_html` returns the same page over
 *   the `srcDoc` path, where the origin is opaque and the cookie cannot ride. If that run
 *   also came back 200, this probe would be measuring something other than what it claims —
 *   the tautology V87 describes one axis over. Both runs must happen, and they must differ.
 * - **The verdicts come from inside the frame**, not from a screenshot. The page writes them
 *   into `data-*` attributes; this reads those. Screenshots are kept as evidence for a human,
 *   not as the oracle.
 *
 * E6 adds three measurements on the frame LibreChat rendered: the iframe height the host gives it
 * and the height the framed document can talk the host into; whether a clipboard write of an
 * `image/png` succeeds from inside it; and whether a blob-URL download fires from inside it. Each
 * has its own control — a wrong-source and a wrong-type message for the height, and the same
 * document unframed for the other two. `resize_probe.mjs` beside this file asks the same three
 * questions with no LibreChat, no Postgres and no Mongo, and isolates the cause of each refusal
 * across a wider matrix of frame policies; its recorded numbers are in
 * `evidence/resize-clipboard-download-probe.json`.
 *
 *   NODE_PATH=<librechat-clone>/node_modules node browser_probe.mjs
 */

import { chromium } from 'playwright';
import { writeFileSync, mkdirSync, readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const EVIDENCE = join(HERE, 'evidence');

const LIBRECHAT = process.env.LIBRECHAT_ORIGIN ?? 'http://chat.noa.internal:3080';
const NOA = process.env.NOA_ORIGIN ?? 'http://noa.internal:8000';
const EMBED = process.env.EMBED_ORIGIN ?? 'http://embed.noa.internal:8000';

const NOA_EMAIL = process.env.NOA_EMAIL ?? 'operator@noa.internal';
const NOA_PASSWORD = process.env.NOA_PASSWORD ?? 'dev-bypass';

const LC_EMAIL = process.env.LC_EMAIL ?? 'operator@noa.internal';
const LC_PASSWORD = process.env.LC_PASSWORD ?? 'Sp1ke-Passw0rd!';
const LC_NAME = 'NOA Operator';

const MCP_SERVER = 'noa';
const TOOL_URI_LIST = `embed_probe_uri_list_mcp_${MCP_SERVER}`;
const TOOL_HTML = `embed_probe_html_mcp_${MCP_SERVER}`;
const TOOL_NOTIFY = `embed_probe_notify_tools_changed_mcp_${MCP_SERVER}`;

/** The height E6 asks the host for, and the absurd one it asks for afterwards. */
const REQUESTED_HEIGHT = 640;
const ABSURD_HEIGHT = 20000;

const results = { startedAt: new Date().toISOString(), stages: {}, failures: [] };

const JSONRPC_LOG = process.env.NOA_JSONRPC_LOG ?? join(HERE, '.runtime', 'jsonrpc.log');

/** Every JSON-RPC method NOA has seen so far, as the probe server recorded it. */
function readJsonRpcLog() {
  if (!existsSync(JSONRPC_LOG)) return [];
  return readFileSync(JSONRPC_LOG, 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((line) => {
      const [at, ...rest] = line.split(' ');
      return { at: Number(at), method: rest.join(' ') };
    });
}

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

/** LibreChat's own auth: register once, then log in. Returns the bearer token its API wants. */
async function librechatSession(request) {
  await request.post(`${LIBRECHAT}/api/auth/register`, {
    data: {
      name: LC_NAME,
      username: 'operator',
      email: LC_EMAIL,
      password: LC_PASSWORD,
      confirm_password: LC_PASSWORD,
    },
    failOnStatusCode: false,
  });

  const login = await request.post(`${LIBRECHAT}/api/auth/login`, {
    data: { email: LC_EMAIL, password: LC_PASSWORD },
  });
  if (!login.ok()) {
    throw new Error(`LibreChat login failed: ${login.status()} ${await login.text()}`);
  }
  const body = await login.json();
  return { token: body.token, user: body.user };
}

/** An agent whose only tools are the probe tools, so the model has one obvious move. */
async function createAgent(request, token) {
  const res = await request.post(`${LIBRECHAT}/api/agents`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      name: 'NOA embed render gate',
      description: 'T59 harness agent',
      provider: 'anthropic',
      model: 'claude-sonnet-5',
      instructions:
        'You operate NOA. When asked to show an approval card, call the tool you are given ' +
        'exactly once with no arguments, then reply with one short sentence. Never invent ' +
        'output.',
      tools: [TOOL_URI_LIST, TOOL_HTML, TOOL_NOTIFY],
    },
  });
  if (!res.ok()) {
    throw new Error(`agent create failed: ${res.status()} ${await res.text()}`);
  }
  return res.json();
}

/** The SPA keeps its access token in the page, not in a cookie, so log in through the form. */
async function librechatUiLogin(page) {
  await page.goto(`${LIBRECHAT}/login`);

  // The API login that created the agent already planted a refresh cookie in this context's
  // jar, so `/login` usually renders for an instant and then bounces into the app. Both
  // outcomes are fine and neither is the thing under test: try the form, and if it goes away
  // underneath us, fall through to waiting for the composer.
  try {
    await page.waitForSelector('input[name="email"]', { timeout: 8000 });
    await page.fill('input[name="email"]', LC_EMAIL);
    await page.fill('input[name="password"]', LC_PASSWORD);
    await page.click('button[type="submit"]');
  } catch {
    // already authenticated, or redirected mid-fill
  }

  await page.waitForSelector('form textarea, #prompt-textarea, textarea', { timeout: 60_000 });
}

/** NOA's session cookie, obtained the way an operator gets it: a login on NOA's origin.
 *
 * Runs in its own tab. The LibreChat tab must not navigate away: its access token lives in
 * the page, and a round trip through another origin drops it back to the login screen —
 * which is a property of this harness, not of the thing under test.
 */
async function noaLogin(page) {
  await page.goto(`${NOA}/health`);
  const status = await page.evaluate(
    async ([origin, email, password]) => {
      const res = await fetch(`${origin}/auth/login`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });
      return { status: res.status, body: (await res.text()).slice(0, 200) };
    },
    [NOA, NOA_EMAIL, NOA_PASSWORD],
  );
  return status;
}

/** Ask the agent for something and wait for the turn to produce what that prompt implies. */
async function runPrompt(page, agentId, prompt, { expectFrame = true } = {}) {
  const url =
    `${LIBRECHAT}/c/new?agent_id=${encodeURIComponent(agentId)}` +
    `&prompt=${encodeURIComponent(prompt)}&submit=true`;
  await page.goto(url);

  if (expectFrame) {
    // The tool-call panel is where ToolCallInfo renders a single UI resource. Waiting on a
    // *sandboxed* iframe rather than on any iframe matters: the app ships a hidden
    // `assets/silence.mp3` frame, and waiting on that would report a render that never
    // happened. If nothing renders, this times out and the run fails.
    await page.waitForSelector('iframe[sandbox]', { timeout: 240_000 });
    await page.waitForTimeout(5000);
    return;
  }

  // No frame to wait on: wait for the tool call to have happened at all, which the message
  // stream shows as assistant text after the call returns.
  await page.waitForTimeout(45_000);
}

/** Everything measurable about the rendered frame, read from the DOM and from inside it. */
async function readFrame(page) {
  const frames = await page.$$eval('iframe', (nodes) =>
    nodes.map((node) => ({
      src: node.getAttribute('src'),
      hasSrcdoc: node.hasAttribute('srcdoc'),
      srcdocLength: (node.getAttribute('srcdoc') ?? '').length,
      sandbox: node.getAttribute('sandbox'),
      csp: node.getAttribute('csp'),
      referrerPolicy: node.getAttribute('referrerpolicy'),
    })),
  );

  const probes = [];
  for (const frame of page.frames()) {
    if (frame === page.mainFrame()) continue;
    try {
      const probe = await frame.evaluate(() => {
        const el = document.getElementById('probe');
        if (!el) return null;
        return {
          mode: el.dataset.mode,
          origin: el.dataset.origin,
          cookieVisible: el.dataset.cookieVisible,
          me: el.dataset.me,
          decide: el.dataset.decide,
          documentOrigin: String(window.location.origin),
        };
      });
      if (probe) probes.push({ frameUrl: frame.url(), ...probe });
    } catch (error) {
      probes.push({ frameUrl: frame.url(), error: String(error) });
    }
  }

  return { frames, probes };
}

/** Inject a gesture target into a document, then read back what the browser lets it do.
 *
 * The NOA-served page has no such button, so the harness supplies one. What is under test is the
 * browser's frame policy, not the button: the click is a real mouse event dispatched at the
 * element's coordinates, and every answer is read back out of a `data-*` attribute the handler
 * wrote — never from the absence of a thrown exception.
 *
 * `scope` is a Frame or a Page, which is what makes the control possible: the same injected code
 * in the same document, once framed and once not. `spikes/.../resize_probe.mjs` asks the same two
 * questions without LibreChat and isolates the cause with a wider matrix of frame policies.
 */
async function probeClipboardAndDownload(page, scope) {
  const context = await scope.evaluate(async () => {
    const el = document.createElement('div');
    el.id = 'noa-surface-probe';
    document.body.appendChild(el);

    const clip = document.createElement('button');
    clip.id = 'noa-surface-clip';
    const dl = document.createElement('button');
    dl.id = 'noa-surface-dl';
    for (const button of [clip, dl]) {
      button.type = 'button';
      button.style.cssText =
        'position:fixed;left:8px;z-index:2147483647;width:180px;height:32px;font:12px sans-serif;';
      document.body.appendChild(button);
    }
    clip.textContent = 'clipboard write';
    clip.style.top = '8px';
    dl.textContent = 'download';
    dl.style.top = '48px';

    // Built now, so the click handler stays synchronous up to `clipboard.write`: awaiting a blob
    // inside the handler is a second, independent reason a write can fail, and it would be
    // indistinguishable from a policy refusal.
    const canvas = document.createElement('canvas');
    canvas.width = 2;
    canvas.height = 2;
    canvas.getContext('2d').fillRect(0, 0, 2, 2);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));

    clip.addEventListener('click', () => {
      el.dataset.clipHasFocus = String(document.hasFocus());
      let item;
      try {
        item = new ClipboardItem({ 'image/png': blob });
      } catch (error) {
        el.dataset.clipResult = 'clipboarditem-threw';
        el.dataset.clipError = `${error && error.name}: ${error && error.message}`;
        return;
      }
      navigator.clipboard.write([item]).then(
        () => {
          el.dataset.clipResult = 'resolved';
          el.dataset.clipError = '(none)';
        },
        (error) => {
          el.dataset.clipResult = 'rejected';
          el.dataset.clipErrorName = (error && error.name) || typeof error;
          el.dataset.clipError = (error && error.message) || String(error);
        },
      );
    });

    dl.addEventListener('click', () => {
      const anchor = document.createElement('a');
      anchor.href = URL.createObjectURL(
        new Blob(['noa embed download probe\n'], { type: 'text/plain' }),
      );
      anchor.download = 'noa-embed-probe.txt';
      document.body.appendChild(anchor);
      anchor.click();
      el.dataset.dlAnchorClicked = '1';
    });

    let supportsPng;
    try {
      supportsPng =
        typeof ClipboardItem !== 'undefined' && typeof ClipboardItem.supports === 'function'
          ? String(ClipboardItem.supports('image/png'))
          : '(ClipboardItem.supports absent)';
    } catch (error) {
      supportsPng = `threw ${error && error.name}`;
    }
    let permission;
    try {
      permission = (await navigator.permissions.query({ name: 'clipboard-write' })).state;
    } catch (error) {
      permission = `query threw ${error && error.name}`;
    }

    return {
      documentOrigin: window.location.origin,
      isSecureContext: window.isSecureContext,
      typeofClipboard: typeof navigator.clipboard,
      typeofClipboardItem: typeof ClipboardItem,
      clipboardItemSupportsImagePng: supportsPng,
      permissionsQueryClipboardWrite: permission,
      pngBlobBytes: blob ? blob.size : -1,
    };
  });

  await scope.locator('#noa-surface-clip').click();
  await page.waitForTimeout(1500);

  const waiting = page
    .waitForEvent('download', { timeout: 6000 })
    .then(async (download) => ({
      fired: true,
      suggestedFilename: download.suggestedFilename(),
      failure: await download.failure(),
    }))
    .catch((error) => ({ fired: false, timeoutError: String(error).split('\n')[0] }));
  await scope.locator('#noa-surface-dl').click();
  const download = await waiting;

  const readback = await scope
    .locator('#noa-surface-probe')
    .evaluate((el) => ({ ...el.dataset }));

  return {
    context,
    clipboardWrite: {
      result: readback.clipResult ?? '(no result recorded)',
      errorName: readback.clipErrorName ?? null,
      errorMessage: readback.clipError ?? null,
      documentHadFocusAtClick: readback.clipHasFocus ?? null,
    },
    download: { anchorClicked: readback.dlAnchorClicked ?? '(not clicked)', ...download },
  };
}

/** The height the host hands the frame, and the height the frame can talk it into.
 *
 * `@mcp-ui/client` writes `height: 100%` inline and then overwrites it from a `ui-size-change`
 * message, gated on `autoResizeIframe` and on `event.source === iframe.contentWindow`. Both gates
 * get a control here, because "the box moved" is only evidence if a wrong-source and a wrong-type
 * message leave it alone.
 */
async function measureRenderedFrameHeight(page, frame) {
  const handle = await page.$(`iframe[src^="${EMBED}"]`);
  if (!handle) return { error: `no iframe with src on ${EMBED}` };

  const box = () =>
    handle.evaluate((el) => {
      const rect = el.getBoundingClientRect();
      return {
        rect: { width: rect.width, height: rect.height },
        inlineStyleWidth: el.style.width || '(unset)',
        inlineStyleHeight: el.style.height || '(unset)',
        sandbox: el.getAttribute('sandbox'),
      };
    });

  const post = async (source, message) => {
    if (source === 'top') await page.evaluate((m) => window.postMessage(m, '*'), message);
    else await frame.evaluate((m) => window.parent.postMessage(m, '*'), message);
    await page.waitForTimeout(500);
    return box();
  };

  const initial = await box();
  const afterWrongSource = await post('top', {
    type: 'ui-size-change',
    payload: { height: REQUESTED_HEIGHT },
  });
  const afterWrongType = await post('frame', {
    type: 'noa-not-a-size-change',
    payload: { height: REQUESTED_HEIGHT },
  });
  const afterSizeChange = await post('frame', {
    type: 'ui-size-change',
    payload: { height: REQUESTED_HEIGHT },
  });
  const afterAbsurdHeight = await post('frame', {
    type: 'ui-size-change',
    payload: { height: ABSURD_HEIGHT },
  });

  return { initial, afterWrongSource, afterWrongType, afterSizeChange, afterAbsurdHeight };
}

async function main() {
  mkdirSync(EVIDENCE, { recursive: true });

  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
  const page = await context.newPage();
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.log(`[browser console] ${msg.text()}`);
  });

  try {
    const session = await librechatSession(context.request);
    record('librechat-session', { userId: session.user?.id, email: session.user?.email });

    const agent = await createAgent(context.request, session.token);
    record('agent', { id: agent.id, model: agent.model, tools: agent.tools });

    const noaPage = await context.newPage();
    const noaSession = await noaLogin(noaPage);
    record('noa-login', noaSession);
    check('NOA login returns 200', noaSession.status === 200, JSON.stringify(noaSession));

    const cookies = await context.cookies();
    const noaCookie = cookies.find((c) => c.name === 'noa_session');
    record('noa-cookie', noaCookie ?? null);
    check('noa_session cookie exists', Boolean(noaCookie));
    check(
      'noa_session is httpOnly, Lax, domain-scoped to .noa.internal',
      noaCookie?.httpOnly === true &&
        noaCookie?.sameSite === 'Lax' &&
        noaCookie?.domain === '.noa.internal',
      JSON.stringify(noaCookie),
    );

    await librechatUiLogin(page);
    record('librechat-ui-login', { url: page.url() });

    // --- E2/E3: the shape under test ---
    await runPrompt(page, agent.id, 'Show me the approval card. Call the uri-list tool.');
    await page.screenshot({ path: join(EVIDENCE, 'uri-list-render.png'), fullPage: true });
    const uriList = await readFrame(page);
    record('uri-list', uriList);

    const srcFrame = uriList.frames.find((f) => (f.src ?? '').startsWith(EMBED));
    check('an iframe was rendered with src on NOA origin', Boolean(srcFrame), JSON.stringify(uriList.frames));
    check('that iframe carries no srcdoc', srcFrame ? !srcFrame.hasSrcdoc : false);
    check(
      'sandbox grants allow-same-origin',
      (srcFrame?.sandbox ?? '').includes('allow-same-origin'),
      srcFrame?.sandbox ?? '(none)',
    );
    check(
      'sandbox withholds allow-forms',
      !(srcFrame?.sandbox ?? '').includes('allow-forms'),
      srcFrame?.sandbox ?? '(none)',
    );

    const uriProbe = uriList.probes.find((p) => p.mode === 'uri-list');
    check('the frame document reported its state', Boolean(uriProbe), JSON.stringify(uriList.probes));
    check(
      'frame document sits on the NOA embed origin',
      uriProbe?.documentOrigin === EMBED,
      uriProbe?.documentOrigin,
    );
    check(
      'session cookie is not readable from script (httpOnly holds)',
      uriProbe?.cookieVisible === '(empty)',
      uriProbe?.cookieVisible,
    );
    check(
      'GET /auth/me from inside the frame is authenticated',
      (uriProbe?.me ?? '').startsWith('200'),
      uriProbe?.me,
    );
    check(
      'POST /embed-probe/decide from inside the frame is authenticated (V22 path)',
      (uriProbe?.decide ?? '').startsWith('200'),
      uriProbe?.decide,
    );

    // --- E6: three properties of the rendered frame a source read cannot settle ---
    //
    // Iframe height, a clipboard write of an image, and a blob download — measured on the frame
    // LibreChat actually rendered, at whatever sandbox string this pin emits.
    //
    // Clipboard permission is granted for the NOA embed origin here, deliberately and narrowly.
    // A headless Chromium refuses `clipboard-write` even to a top-level page, which would make
    // the framed refusal unattributable: control and subject would both fail and the comparison
    // would prove nothing. `resize_probe.mjs` measured that the framed refusal survives the grant
    // while the unframed control starts succeeding, so granting is what gives this comparison
    // discriminating power rather than what produces the result.
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: EMBED });

    const embedFrame = page.frames().find((f) => f.url().startsWith(EMBED));
    const framedSurfaces = embedFrame
      ? await probeClipboardAndDownload(page, embedFrame)
      : { error: 'no frame on the NOA embed origin' };

    // The control: the SAME document, not framed, in its own tab so the LibreChat tab keeps its
    // in-page access token. If the framed refusals showed up here too, this stage would be
    // measuring the browser's global posture instead of the frame policy.
    const topLevelPage = await context.newPage();
    await topLevelPage.goto(embedFrame ? embedFrame.url() : `${EMBED}/embed-probe/frame`);
    const topLevelSurfaces = await probeClipboardAndDownload(topLevelPage, topLevelPage);
    await topLevelPage.close();

    const heights = embedFrame
      ? await measureRenderedFrameHeight(page, embedFrame)
      : { error: 'no frame on the NOA embed origin' };

    record('rendered-frame-surfaces', {
      grantedPermissions: `context.grantPermissions(['clipboard-read','clipboard-write']) for ${EMBED} only`,
      requestedHeightPx: REQUESTED_HEIGHT,
      absurdHeightPx: ABSURD_HEIGHT,
      heights,
      framed: framedSurfaces,
      topLevelControl: topLevelSurfaces,
      note:
        'A blocked download is silent in a headless Chromium: the anchor click throws nothing ' +
        'and no console message is emitted, so `fired: false` from the download event is the ' +
        'only signal. A headed run does log "Download is disallowed. The frame initiating or ' +
        'instantiating the download is sandboxed, but the flag ‘allow-downloads’ is ' +
        'not set."',
    });

    check(
      'E6: the rendered frame has a measurable initial height',
      typeof heights?.initial?.rect?.height === 'number' && heights.initial.rect.height > 0,
      JSON.stringify(heights?.initial),
    );
    check(
      'E6: control — ui-size-change from the TOP window does not move the frame (source gate holds)',
      heights?.afterWrongSource?.rect?.height === heights?.initial?.rect?.height,
      `${heights?.initial?.rect?.height} -> ${heights?.afterWrongSource?.rect?.height}`,
    );
    check(
      'E6: control — an unrecognised message type from the frame does not move it',
      heights?.afterWrongType?.rect?.height === heights?.initial?.rect?.height,
      `${heights?.initial?.rect?.height} -> ${heights?.afterWrongType?.rect?.height}`,
    );
    check(
      `E6: ui-size-change from the frame sets the height verbatim (${REQUESTED_HEIGHT}px)`,
      heights?.afterSizeChange?.rect?.height === REQUESTED_HEIGHT,
      `${heights?.afterSizeChange?.rect?.height} / inline ${heights?.afterSizeChange?.inlineStyleHeight}`,
    );
    check(
      'E6: width is untouched when the payload omits it',
      heights?.afterSizeChange?.inlineStyleWidth === '100%' &&
        heights?.afterSizeChange?.rect?.width === heights?.initial?.rect?.width,
      `inline ${heights?.afterSizeChange?.inlineStyleWidth}, ${heights?.initial?.rect?.width} -> ${heights?.afterSizeChange?.rect?.width}`,
    );
    check(
      `E6: an absurd height (${ABSURD_HEIGHT}px) is applied verbatim — the host does not clamp, so any bound has to come from the framed document`,
      heights?.afterAbsurdHeight?.rect?.height === ABSURD_HEIGHT,
      `${heights?.afterAbsurdHeight?.rect?.height} / inline ${heights?.afterAbsurdHeight?.inlineStyleHeight}`,
    );

    check(
      'E6: the framed document is a secure context, so a refusal is not the scheme',
      framedSurfaces?.context?.isSecureContext === true,
      JSON.stringify(framedSurfaces?.context),
    );
    check(
      'E6: control — clipboard write of image/png SUCCEEDS in the same document unframed',
      topLevelSurfaces?.clipboardWrite?.result === 'resolved',
      `${topLevelSurfaces?.clipboardWrite?.result}: ${topLevelSurfaces?.clipboardWrite?.errorMessage}`,
    );
    check(
      'E6: clipboard write of image/png from inside the rendered frame is REFUSED',
      framedSurfaces?.clipboardWrite?.result === 'rejected',
      `${framedSurfaces?.clipboardWrite?.result}: ${framedSurfaces?.clipboardWrite?.errorMessage}`,
    );
    check(
      'E6: control — a blob download FIRES in the same document unframed',
      topLevelSurfaces?.download?.fired === true,
      JSON.stringify(topLevelSurfaces?.download),
    );
    check(
      'E6: a blob download from inside the rendered frame does NOT fire',
      framedSurfaces?.download?.fired === false &&
        framedSurfaces?.download?.anchorClicked === '1',
      JSON.stringify(framedSurfaces?.download),
    );

    // --- E4: the control that proves the above discriminates ---
    await runPrompt(page, agent.id, 'Now call the html tool to show the inline card.');
    await page.screenshot({ path: join(EVIDENCE, 'html-control-render.png'), fullPage: true });
    const html = await readFrame(page);
    record('html-control', html);

    const srcdocProbe = html.probes.find((p) => p.mode === 'srcdoc');
    const srcdocFrame = html.frames.find((f) => f.hasSrcdoc);
    check('the control rendered through srcdoc', Boolean(srcdocFrame), JSON.stringify(html.frames));
    check(
      'control: the same requests are NOT authenticated',
      srcdocProbe == null ||
        (!(srcdocProbe.me ?? '').startsWith('200') && !(srcdocProbe.decide ?? '').startsWith('200')),
      JSON.stringify(srcdocProbe),
    );

    // --- E5: item (f) ---
    const beforeNotify = readJsonRpcLog();
    await runPrompt(page, agent.id, 'Call the notify tool once.', { expectFrame: false });
    const afterNotify = readJsonRpcLog();

    const newMethods = afterNotify.slice(beforeNotify.length);
    const notifyIndex = newMethods.findIndex((entry) => entry.method === 'tools/call');
    const listAfterNotify =
      notifyIndex >= 0 &&
      newMethods.slice(notifyIndex + 1).some((entry) => entry.method === 'tools/list');

    record('notify', {
      methodsAfterPrompt: newMethods,
      toolsListAfterNotification: listAfterNotify,
      note:
        'NOA emitted notifications/tools/list_changed from inside the tool call. A ' +
        'tools/list arriving afterwards would mean LibreChat honours it.',
    });
    check(
      'the notify tool call reached NOA',
      notifyIndex >= 0,
      JSON.stringify(newMethods),
    );
  } catch (error) {
    results.failures.push({ name: 'run', detail: String(error) });
    console.log(`FAIL  run: ${error}`);
    await page.screenshot({ path: join(EVIDENCE, 'failure.png'), fullPage: true }).catch(() => {});
  } finally {
    results.finishedAt = new Date().toISOString();
    writeFileSync(join(EVIDENCE, 'probe-results.json'), JSON.stringify(results, null, 2));
    await browser.close();
  }

  if (results.failures.length > 0) {
    console.log(`\n${results.failures.length} failed check(s)`);
    process.exit(1);
  }
  console.log('\nall checks green');
}

await main();
