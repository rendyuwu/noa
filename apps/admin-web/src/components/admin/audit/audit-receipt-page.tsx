'use client'

import Link from 'next/link'
import { useEffect, useMemo, useRef, useState } from 'react'

import { PageHeader } from '@gio/bigsu-app-shell'
import { Button, Card, StatusChip } from '@gio/bigsu-ui'

import { fetchReceiptPayload } from '@/lib/admin/audit/audit-api'
import { toMessage } from '@/lib/admin/shared/error-message'
import { isAuthRedirectError } from '@/lib/auth/session'
import {
  buildWorkflowReceiptPlaintext,
  buildWorkflowReceiptSections,
  parseWorkflowReceiptPayload,
} from '@/lib/admin/audit/workflow-receipt'
import {
  canWriteClipboardImage,
  canWriteClipboardText,
  captureElementToPngBlob,
  copyPlaintext,
  copyPngBlobToClipboard,
  downloadBlob,
  downloadTextFile,
} from '@/lib/admin/audit/receipt-image-export'
import { DetailSections } from '@/components/admin/audit/detail-sections'

type Flash = 'idle' | 'done' | 'failed'

function useFlash(): [Flash, (next: Flash) => void] {
  const [state, setState] = useState<Flash>('idle')
  const set = (next: Flash) => {
    setState(next)
    if (next !== 'idle') window.setTimeout(() => setState('idle'), 1400)
  }
  return [state, set]
}

function flashLabel(state: Flash, idle: string, done: string): string {
  if (state === 'done') return done
  if (state === 'failed') return 'Failed'
  return idle
}

// Standalone, export-friendly receipt view for one action request (issue #111).
// The receipt payload is redacted server-side AND re-parsed through the shared
// workflow-receipt data layer, which redacts sensitive evidence a second time —
// so every export path (plaintext copy, .txt download, PNG image) is built from
// already-redacted content and can never leak a secret or a signed delivery URL.
// The PNG is a capture of the same redacted DOM. The status renders through
// StatusChip; there is no edit or delete affordance — audit history is
// append-only.
export function AuditReceiptPage({ actionRequestId }: { actionRequestId: string }) {
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const captureRef = useRef<HTMLDivElement>(null)
  const loadSeq = useRef(0)
  const [copyText, setCopyText] = useFlash()
  const [download, setDownload] = useFlash()
  const [copyImage, setCopyImage] = useFlash()
  const [imageBusy, setImageBusy] = useState(false)

  useEffect(() => {
    const seq = ++loadSeq.current
    void (async () => {
      // Reset transient state inside the async body (not synchronously in the
      // effect) so a stale payload never lingers when actionRequestId changes.
      setLoading(true)
      setLoadError(null)
      setPayload(null)
      try {
        const receipt = await fetchReceiptPayload(actionRequestId)
        if (seq !== loadSeq.current) return
        setPayload(receipt)
      } catch (error) {
        if (seq !== loadSeq.current || isAuthRedirectError(error)) return
        setLoadError(toMessage(error, 'Unable to load receipt'))
      } finally {
        if (seq === loadSeq.current) setLoading(false)
      }
    })()
    return () => {
      loadSeq.current += 1
    }
  }, [actionRequestId])

  const parsed = useMemo(() => (payload ? parseWorkflowReceiptPayload(payload) : null), [payload])
  const sections = useMemo(() => (parsed ? buildWorkflowReceiptSections(parsed) : []), [parsed])
  const plaintext = useMemo(() => (parsed ? buildWorkflowReceiptPlaintext(parsed) : ''), [parsed])
  const fileStem = `receipt-${parsed?.actionRequestId ?? actionRequestId}`

  const onCopyText = async () => {
    try {
      await copyPlaintext(plaintext)
      setCopyText('done')
    } catch {
      setCopyText('failed')
    }
  }

  const onDownloadText = () => {
    try {
      downloadTextFile(plaintext, `${fileStem}.txt`)
      setDownload('done')
    } catch {
      setDownload('failed')
    }
  }

  const onCopyImage = async () => {
    if (!captureRef.current) return
    setImageBusy(true)
    try {
      const blob = captureElementToPngBlob(captureRef.current)
      await copyPngBlobToClipboard(blob)
      setCopyImage('done')
    } catch {
      setCopyImage('failed')
    } finally {
      setImageBusy(false)
    }
  }

  const onDownloadImage = async () => {
    if (!captureRef.current) return
    setImageBusy(true)
    try {
      const blob = await captureElementToPngBlob(captureRef.current)
      downloadBlob(blob, `${fileStem}.png`)
    } catch {
      setCopyImage('failed')
    } finally {
      setImageBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        breadcrumb={[
          { label: 'Administration', href: '/admin' },
          { label: 'Audit', href: '/admin/audit' },
          { label: 'Receipt' },
        ]}
        title="Receipt"
        description="Standalone, export-friendly receipt view. Sensitive fields are redacted from every export."
      />

      <div className="mt-6 flex flex-col gap-4">
        {loadError ? (
          <Card className="border-status-danger-border p-4 text-sm text-status-danger" role="alert">
            <div className="flex items-center justify-between gap-3">
              <span className="min-w-0">{loadError}</span>
              <Button size="sm" variant="secondary" onClick={() => window.location.reload()}>
                Reload
              </Button>
            </div>
          </Card>
        ) : null}

        {parsed ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void onCopyText()}
                disabled={!canWriteClipboardText()}
              >
                {flashLabel(copyText, 'Copy', 'Copied')}
              </Button>
              <Button variant="outline" size="sm" onClick={onDownloadText}>
                {flashLabel(download, 'Download', 'Downloaded')}
              </Button>
              {canWriteClipboardImage() ? (
                <Button variant="ghost" size="sm" onClick={() => void onCopyImage()} disabled={imageBusy}>
                  {flashLabel(copyImage, 'Copy image', 'Copied')}
                </Button>
              ) : null}
              <Button variant="ghost" size="sm" onClick={() => void onDownloadImage()} disabled={imageBusy}>
                Save image
              </Button>
            </div>

            <Card className="mx-auto w-full max-w-3xl p-5" aria-label="Workflow receipt">
              <div ref={captureRef} data-receipt-capture className="min-w-0">
                <div className="flex items-start justify-between gap-3">
                  <span className="min-w-0 truncate text-base font-semibold text-text-primary">
                    {parsed.replyTemplate.title}
                  </span>
                  <StatusChip status={parsed.badge.status} />
                </div>
                <p className="mt-2 text-sm text-text-secondary">{parsed.replyTemplate.summary}</p>
                <div className="mt-3">
                  <DetailSections sections={sections} showEmptyState />
                </div>
              </div>
            </Card>
          </>
        ) : (
          <Card className="mx-auto w-full max-w-3xl p-6 text-sm text-text-secondary">
            {loading ? 'Loading receipt…' : 'Receipt unavailable.'}
          </Card>
        )}

        <div>
          <Link
            href="/admin/audit"
            className="text-sm text-text-secondary underline underline-offset-4 hover:text-text-primary"
          >
            Back to Audit
          </Link>
        </div>
      </div>
    </>
  )
}
