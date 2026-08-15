'use client'

// Receipt export helpers (issue #105). Clipboard + PNG capture for the workflow
// receipt card. Redaction is enforced upstream at the DATA layer
// (workflow-receipt.ts), so every export path here — plaintext copy, text
// download, and the PNG image — operates on already-redacted content and can
// never leak a secret or a signed delivery URL. These helpers only move bytes;
// they never re-read the raw wire payload.

const waitForNextFrame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))

async function waitForFonts(): Promise<void> {
  try {
    await (document as unknown as { fonts?: { ready?: Promise<unknown> } }).fonts?.ready
  } catch {
    // Font readiness is best-effort; capture proceeds either way.
  }
}

export function canWriteClipboardText(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    typeof navigator.clipboard?.writeText === 'function'
  )
}

export async function copyPlaintext(text: string): Promise<void> {
  if (!canWriteClipboardText()) throw new Error('Clipboard text write unsupported')
  await navigator.clipboard.writeText(text)
}

export function downloadTextFile(text: string, fileName: string): void {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fileName
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function canWriteClipboardImage(): boolean {
  if (typeof window === 'undefined' || !window.isSecureContext) return false
  if (typeof navigator === 'undefined' || !navigator.clipboard?.write) return false
  if (typeof ClipboardItem === 'undefined') return false

  const supports = (ClipboardItem as unknown as { supports?: (type: string) => boolean }).supports
  if (typeof supports === 'function') {
    try {
      if (!supports('image/png')) return false
    } catch {
      // Ignore support probe failures; the write attempt is the real gate.
    }
  }
  return true
}

// Capture an on-screen element (the already-redacted receipt card) to a PNG.
// The dynamic import keeps html-to-image out of the critical bundle and only
// loads it when the operator actually exports an image.
export async function captureElementToPngBlob(element: HTMLElement): Promise<Blob> {
  await waitForFonts()
  await waitForNextFrame()

  const { toBlob } = await import('html-to-image')
  const backgroundColor = (() => {
    try {
      return getComputedStyle(element).backgroundColor || '#ffffff'
    } catch {
      return '#ffffff'
    }
  })()

  const blob = await toBlob(element, { cacheBust: true, backgroundColor, pixelRatio: 2 })
  if (!blob) throw new Error('Failed to render receipt PNG')
  return blob
}

export async function copyPngBlobToClipboard(blob: Blob | Promise<Blob>): Promise<void> {
  if (!canWriteClipboardImage()) throw new Error('Clipboard image write unsupported')
  // Passing a Promise<Blob> keeps the write inside the user gesture even when
  // capture is async, which some browsers require.
  await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
}

export function downloadBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fileName
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
