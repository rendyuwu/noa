"use client";

type CaptureOptions = {
  pixelRatio?: number;
  backgroundColor?: string;
};

const waitForNextFrame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

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
  if (typeof navigator === "undefined") return false;
  return Boolean(navigator.clipboard?.write) && typeof ClipboardItem !== "undefined";
}

export async function copyPngBlobToClipboard(blob: Blob): Promise<void> {
  if (!canWriteClipboardPng()) {
    throw new Error("Clipboard image write unsupported");
  }
  await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
}

export function downloadBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(url);
}

export async function captureElementToPngBlob(
  el: HTMLElement,
  { pixelRatio, backgroundColor }: CaptureOptions = {},
): Promise<Blob> {
  await waitForFonts();
  await waitForImages(el);
  await waitForNextFrame();

  const { toBlob } = await import("html-to-image");

  const resolvedBg =
    backgroundColor ??
    (() => {
      try {
        return getComputedStyle(document.body).backgroundColor;
      } catch {
        return "#000";
      }
    })();

  const blob = await toBlob(el, {
    cacheBust: true,
    backgroundColor: resolvedBg,
    pixelRatio: pixelRatio ?? (typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1),
  });

  if (!blob) {
    throw new Error("Failed to render PNG blob");
  }
  return blob;
}
