"use client";

type CaptureOptions = {
  pixelRatio?: number;
  backgroundColor?: string;
};

const waitForNextFrame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

function resolvePixelRatio(
  el: HTMLElement,
  requestedPixelRatio: number | undefined,
): number {
  const base =
    requestedPixelRatio ??
    (typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1);

  // Keep Firefox/Chromium canvas sizes in a safer range.
  // (Browsers vary by OS/GPU; this is a pragmatic cap to reduce failures.)
  const MAX_SIDE_PX = 8192;
  const MAX_AREA_PX = 64_000_000;

  try {
    const rect = el.getBoundingClientRect();
    const w = Math.max(1, rect.width);
    const h = Math.max(1, rect.height);

    const capBySide = Math.min(MAX_SIDE_PX / w, MAX_SIDE_PX / h);
    const capByArea = Math.sqrt(MAX_AREA_PX / (w * h));
    const capped = Math.min(base, capBySide, capByArea);

    // Avoid going below 1 unless explicitly requested.
    return Math.max(1, Number.isFinite(capped) ? capped : 1);
  } catch {
    return Math.max(1, base);
  }
}

async function dataUrlToBlob(dataUrl: string): Promise<Blob> {
  const response = await fetch(dataUrl);
  return await response.blob();
}

async function waitForFonts(): Promise<void> {
  try {
    await (document as any).fonts?.ready;
  } catch {
    // no-op
  }
}

async function waitForImages(root: HTMLElement): Promise<void> {
  const images = Array.from(root.querySelectorAll("img"));
  if (images.length === 0) return;
  await Promise.all(
    images.map((img) =>
      img.complete
        ? Promise.resolve()
        : new Promise<void>((resolve) => {
            img.onload = () => resolve();
            img.onerror = () => resolve();
          }),
    ),
  );
}

export function canWriteClipboardPng(): boolean {
  if (typeof window === "undefined") return false;
  if (!window.isSecureContext) return false;
  if (typeof navigator === "undefined") return false;
  if (!navigator.clipboard?.write) return false;
  if (typeof ClipboardItem === "undefined") return false;

  const supports = (ClipboardItem as unknown as { supports?: (type: string) => boolean }).supports;
  if (typeof supports === "function") {
    try {
      if (!supports("image/png")) return false;
    } catch {
      // ignore
    }
  }
  return true;
}

export async function copyPngBlobToClipboard(blob: Blob | Promise<Blob>): Promise<void> {
  if (!canWriteClipboardPng()) {
    throw new Error("Clipboard image write unsupported");
  }
  // Important: pass a Promise<Blob> through ClipboardItem to keep the write
  // associated with the user gesture, even if rendering/capture is async.
  await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
}

export function downloadBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function captureElementToPngBlob(
  el: HTMLElement,
  { pixelRatio, backgroundColor }: CaptureOptions = {},
): Promise<Blob> {
  // Some browsers (notably Firefox) are more likely to fail with `contain:*` on
  // the capture root. Temporarily override to improve export reliability.
  const prevContain = el.style.contain;
  try {
    el.style.contain = "none";
  } catch {
    // ignore
  }

  try {
    await waitForFonts();
    await waitForImages(el);
    await waitForNextFrame();

    const { toBlob, toPng } = await import("html-to-image");

    const resolvedBg =
      backgroundColor ??
      (() => {
        try {
          return getComputedStyle(document.body).backgroundColor;
        } catch {
          return "#000";
        }
      })();

    const resolvedPixelRatio = resolvePixelRatio(el, pixelRatio);

    const attempt = async (ratio: number): Promise<Blob | null> => {
      const blob = await toBlob(el, {
        cacheBust: true,
        backgroundColor: resolvedBg,
        pixelRatio: ratio,
      });
      return blob;
    };

    try {
      const blob = await attempt(resolvedPixelRatio);
      if (blob) return blob;
    } catch {
      // fall through
    }

    // Retry with a conservative ratio.
    try {
      const blob = await attempt(1);
      if (blob) return blob;
    } catch {
      // fall through
    }

    // Fallback to a data URL path when Blob rendering fails (observed in Firefox).
    try {
      const dataUrl = await toPng(el, {
        cacheBust: true,
        backgroundColor: resolvedBg,
        pixelRatio: 1,
      });
      const blob = await dataUrlToBlob(dataUrl);
      if (!blob) throw new Error("Failed to convert PNG data URL");
      return blob;
    } catch (error) {
      throw new Error("Failed to render PNG blob", { cause: error as any });
    }
  } finally {
    try {
      el.style.contain = prevContain;
    } catch {
      // ignore
    }
  }
}
