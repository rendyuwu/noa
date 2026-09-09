/**
 * Browser half of `resize_probe.mjs`. Bundled by esbuild into `.runtime/probe_bundle.js`.
 *
 * This mounts the REAL `@mcp-ui/client` `UIResourceRenderer` — the same bytes LibreChat ships —
 * with the same props LibreChat passes it, so the heights this reports are the heights the
 * package produces rather than heights re-derived from reading it.
 *
 * Two mount APIs, and the distinction matters when reading the artifact:
 *
 * - `mount()` goes through `UIResourceRenderer`. The sandbox string it emits is a MEASUREMENT,
 *   not an input.
 * - `mountRaw()` builds a bare `<iframe>` with a sandbox string supplied by the caller. It exists
 *   only so the clipboard and download questions can be asked at the exact strings `mount()` was
 *   just measured to produce, plus at deliberately-altered strings that serve as controls.
 *   Nothing about the frame policy is assumed here; it is passed in from what was measured.
 */

import React from 'react';
import { createRoot } from 'react-dom/client';
import { UIResourceRenderer } from '@mcp-ui/client';

/** Every `message` the host document receives, whoever sent it.
 *
 * This is the negative control's oracle. `UIResourceRenderer` only reacts to messages whose
 * `event.source` is its own `iframe.contentWindow`, so when the resize-disabled case reports an
 * unchanged height there are two possible stories: the resize was refused, or the message never
 * arrived. A log that is agnostic about the sender separates them — a receipt here beside an
 * unchanged height is a refusal, and an empty log would mean the run measured nothing.
 */
const received = [];
window.addEventListener('message', (event) => {
  received.push({
    origin: event.origin,
    data: event.data,
    isFrameSource: event.source !== window,
    at: Date.now(),
  });
});

const actions = [];
const roots = new Map();

/** Every drag event the HOST document sees on its own drop target.
 *
 * This is the oracle for the drag-out question. A drop that never arrives is silent — nothing
 * throws in the frame and nothing throws here — so the claim has to rest on this log being empty
 * while the same drag lands on a target INSIDE the frame. `kinds` and `types` are read
 * synchronously in the handler because a `DataTransfer` is only readable during the event.
 */
const dropLog = [];

function installHostDropZone() {
  if (document.getElementById('hostdrop')) return;
  const zone = document.createElement('div');
  zone.id = 'hostdrop';
  zone.className = 'lc-dropzone';
  zone.textContent = 'host drop target';
  for (const type of ['dragenter', 'dragover', 'dragleave', 'drop']) {
    zone.addEventListener(type, (event) => {
      // Required for `drop` to fire at all; without it the browser refuses the drop and the
      // empty log would mean "this page declined" rather than "nothing crossed the boundary".
      event.preventDefault();
      const dt = event.dataTransfer;
      const entry = { event: type, at: Date.now() };
      if (!dt) {
        entry.dataTransfer = null;
      } else {
        entry.kinds = Array.from(dt.items).map((item) => item.kind);
        entry.itemTypes = Array.from(dt.items).map((item) => item.type);
        entry.types = Array.from(dt.types);
        entry.fileCount = dt.files.length;
        if (type === 'drop') {
          entry.data = {
            'text/uri-list': dt.getData('text/uri-list').slice(0, 400),
            'text/html': dt.getData('text/html').slice(0, 400),
            'text/plain': dt.getData('text/plain').slice(0, 400),
          };
          entry.fileNames = Array.from(dt.files).map((f) => `${f.name} (${f.type}, ${f.size}B)`);
        }
      }
      dropLog.push(entry);
    });
  }
  document.body.appendChild(zone);
}

function slotsHost() {
  return document.getElementById('slots');
}

/** The two live render sites, reproduced as DOM rather than described in prose.
 *
 * Tailwind is not available in this harness, so the utility classes are hand-written in
 * `host.html`. The class names there carry the original Tailwind names in a comment.
 */
function wrapperFor(mode, slot) {
  if (mode === 'mcpui-resource-span') {
    // `client/src/components/MCPUIResource/MCPUIResource.tsx:39` —
    // `<span className="mx-1 inline-block w-full align-middle">`, an auto-height inline-block.
    const block = document.createElement('div');
    block.className = 'lc-message-body';
    const span = document.createElement('span');
    span.className = 'lc-mcpui-span';
    span.setAttribute('data-slot', slot);
    block.appendChild(span);
    slotsHost().appendChild(block);
    return span;
  }
  if (mode === 'toolcall-bare') {
    // `client/src/components/Chat/Messages/Content/ToolCallInfo.tsx:167-173` — the renderer is
    // mounted with no wrapper of its own, straight into the panel body at `:132`,
    // `<div className="w-full px-3 py-3.5">`.
    const div = document.createElement('div');
    div.className = 'lc-toolcall-body';
    div.setAttribute('data-slot', slot);
    slotsHost().appendChild(div);
    return div;
  }
  throw new Error(`unknown wrapper mode: ${mode}`);
}

function styleSnapshot(node) {
  const computed = window.getComputedStyle(node);
  const rect = node.getBoundingClientRect();
  return {
    rect: { width: rect.width, height: rect.height },
    inlineStyleWidth: node.style.width || '(unset)',
    inlineStyleHeight: node.style.height || '(unset)',
    computedWidth: computed.width,
    computedHeight: computed.height,
    computedDisplay: computed.display,
    computedBorderTopWidth: computed.borderTopWidth,
    computedBoxSizing: computed.boxSizing,
  };
}

window.__harness = {
  received,
  actions,
  dropLog,

  clearDropLog() {
    dropLog.length = 0;
    return dropLog.length;
  },

  reset() {
    for (const root of roots.values()) {
      try {
        root.unmount();
      } catch {
        /* a root whose container is already gone */
      }
    }
    roots.clear();
    slotsHost().innerHTML = '';
    received.length = 0;
    actions.length = 0;
  },

  /** Mount `UIResourceRenderer` with LibreChat's props. `htmlProps` keys are omitted when the
   * spec omits them, so "absent" is really absent rather than `undefined` — the resize gate in
   * the package reads truthiness, and an explicit `undefined` would be indistinguishable from
   * absent only by accident. */
  mount(spec) {
    const container = wrapperFor(spec.wrapper, spec.slot);
    const htmlProps = {};
    if (spec.autoResizeIframe !== undefined) htmlProps.autoResizeIframe = spec.autoResizeIframe;
    if (spec.sandboxPermissions !== undefined) htmlProps.sandboxPermissions = spec.sandboxPermissions;

    const root = createRoot(container);
    roots.set(spec.slot, root);
    root.render(
      React.createElement(UIResourceRenderer, {
        resource: {
          uri: spec.uri ?? `ui://noa/probe/${spec.slot}`,
          mimeType: 'text/uri-list',
          text: spec.url,
        },
        onUIAction: async (result) => {
          actions.push(result);
          return { handled: true };
        },
        htmlProps,
      }),
    );
    return { slot: spec.slot, htmlPropKeys: Object.keys(htmlProps) };
  },

  /** A bare frame at a caller-supplied policy. Used for the clipboard and download questions. */
  mountRaw(spec) {
    const container = document.createElement('div');
    container.className = 'lc-raw-slot';
    container.setAttribute('data-slot', spec.slot);
    slotsHost().appendChild(container);

    const iframe = document.createElement('iframe');
    iframe.setAttribute('title', `raw probe frame ${spec.slot}`);
    if (spec.sandbox !== null && spec.sandbox !== undefined) iframe.setAttribute('sandbox', spec.sandbox);
    if (spec.allow) iframe.setAttribute('allow', spec.allow);
    iframe.style.width = `${spec.width ?? 420}px`;
    iframe.style.height = `${spec.height ?? 240}px`;
    iframe.src = spec.url;
    container.appendChild(iframe);

    return {
      slot: spec.slot,
      sandboxAttribute: iframe.getAttribute('sandbox'),
      hasSandboxAttribute: iframe.hasAttribute('sandbox'),
      allowAttribute: iframe.getAttribute('allow'),
    };
  },

  /** Everything measurable about a slot's frame and its containing block. */
  measure(slot) {
    const container = document.querySelector(`[data-slot="${slot}"]`);
    if (!container) return { slot, error: 'slot not found' };
    const iframe = container.querySelector('iframe');
    if (!iframe) return { slot, error: 'no iframe in slot' };
    return {
      slot,
      sandboxAttribute: iframe.getAttribute('sandbox'),
      hasSandboxAttribute: iframe.hasAttribute('sandbox'),
      allowAttribute: iframe.getAttribute('allow'),
      src: iframe.getAttribute('src'),
      iframe: styleSnapshot(iframe),
      container: styleSnapshot(container),
    };
  },
};

installHostDropZone();

window.__harness.ready = true;
