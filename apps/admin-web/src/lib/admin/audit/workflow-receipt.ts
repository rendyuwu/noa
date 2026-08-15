// Workflow receipt parsing + redaction.
//
// Redaction happens at the DATA layer, not in the card: a receipt has three
// export paths — rendered DOM, clipboard/plaintext, and PNG capture — and an
// earlier renderer that redacted in only one of them leaked through the other
// two. Doing it here means no export path can carry a secret, because none of
// them ever sees one. Callers opt back in per item only when the wire marks it
// `sensitiveApproved` (explicit + approved).

import type {
  ReceiptEvidenceItem,
  ReceiptEvidenceSection,
} from './receipt-evidence'
import { coerceRecord, coerceString } from '@/lib/admin/shared/coerce'

export type ReceiptOutcome = 'changed' | 'partial' | 'no_op' | 'failed' | 'denied' | 'info'

export type ReceiptStatusLabel = 'SUCCESS' | 'PARTIAL' | 'NO-OP' | 'FAILED' | 'DENIED'

// Map a receipt outcome onto the app's 11-value BIGSU status vocabulary so the
// card renders a real StatusChip rather than an ad-hoc coloured pill.
export type ReceiptBadge = {
  label: ReceiptStatusLabel
  status: 'Completed' | 'In Review' | 'Failed' | 'Rejected' | 'Archived'
}

export type ReceiptReplyTemplate = {
  title: string
  outcome: ReceiptOutcome
  summary: string
  nextStep: string | null
}

export type WorkflowReceiptParsed = {
  replyTemplate: ReceiptReplyTemplate
  badge: ReceiptBadge
  evidenceSections: ReceiptEvidenceSection[]
  actionRequestId?: string
  threadId?: string
  toolRunId?: string
  receiptId?: string
  toolName?: string
  workflowFamily?: string
  terminalPhase?: string
  generatedAt?: string
  errorCode?: string
}

export const REDACTED_PLACEHOLDER = '[redacted]'

// Labels whose value is a credential/secret by definition.
const SENSITIVE_LABEL = /(pass(word|wd)?|secret|token|api[\s_-]?key|authorization|bearer|private[\s_-]?key|credential|session|cookie|otp|mfa)/i

// Signed / presigned delivery URLs carry an embedded capability in the query
// string — sharing the receipt would share the capability. Also catch raw AWS
// keys and PEM blocks that occasionally land in tool output.
const SENSITIVE_VALUE =
  /(https?:\/\/[^\s]*[?&](x-amz-signature|signature|sig|token|se|sp|sig=)[^\s]*|AKIA[0-9A-Z]{16}|-----BEGIN[^-]+PRIVATE KEY-----)/i

export function isSensitiveEvidence(item: ReceiptEvidenceItem): boolean {
  return SENSITIVE_LABEL.test(item.label) || SENSITIVE_VALUE.test(item.value)
}

function isApproved(value: unknown): boolean {
  const record = coerceRecord(value)
  return record?.sensitiveApproved === true
}

// Redact one item. Approved-sensitive items pass through untouched; every other
// sensitive item keeps its label (so the reader knows a field exists) but the
// value becomes the placeholder.
export function redactEvidenceItem(
  item: ReceiptEvidenceItem,
  raw?: unknown,
): ReceiptEvidenceItem {
  if (isApproved(raw)) return item
  return isSensitiveEvidence(item) ? { label: item.label, value: REDACTED_PLACEHOLDER } : item
}

export function redactEvidenceSections(
  sections: ReceiptEvidenceSection[],
): ReceiptEvidenceSection[] {
  return sections.map((section) => ({
    title: section.title,
    items: section.items.map((item) => redactEvidenceItem(item)),
  }))
}

function coerceReplyTemplate(value: unknown): ReceiptReplyTemplate | undefined {
  const record = coerceRecord(value)
  const title = coerceString(record?.title)
  const summary = coerceString(record?.summary)
  const outcome = coerceString(record?.outcome)
  const nextStep = coerceString(record?.nextStep)
  const isOutcome =
    outcome === 'changed' ||
    outcome === 'partial' ||
    outcome === 'no_op' ||
    outcome === 'failed' ||
    outcome === 'denied' ||
    outcome === 'info'
  if (!title || !summary || !isOutcome) return undefined
  return { title, summary, outcome, nextStep: nextStep ?? null }
}

function coerceEvidenceSections(value: unknown): ReceiptEvidenceSection[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((entry) => {
    const record = coerceRecord(entry)
    const title = coerceString(record?.title)
    const rawItems = Array.isArray(record?.items) ? record.items : []
    const items = rawItems.flatMap((raw) => {
      const itemRecord = coerceRecord(raw)
      const label = coerceString(itemRecord?.label)
      const rawValue = itemRecord?.value
      if (!label) return []
      const value =
        typeof rawValue === 'string'
          ? rawValue
          : typeof rawValue === 'number' || typeof rawValue === 'boolean'
            ? String(rawValue)
            : undefined
      if (value === undefined) return []
      // Redact at parse time so no downstream consumer ever sees the secret.
      return [redactEvidenceItem({ label, value }, itemRecord)]
    })
    return title && items.length > 0 ? [{ title, items }] : []
  })
}

export function getReceiptBadge(outcome: ReceiptOutcome): ReceiptBadge {
  switch (outcome) {
    case 'changed':
      return { label: 'SUCCESS', status: 'Completed' }
    case 'partial':
      return { label: 'PARTIAL', status: 'In Review' }
    case 'failed':
      return { label: 'FAILED', status: 'Failed' }
    case 'denied':
      return { label: 'DENIED', status: 'Rejected' }
    case 'no_op':
    case 'info':
      return { label: 'NO-OP', status: 'Archived' }
  }
}

export function parseWorkflowReceiptPayload(payload: unknown): WorkflowReceiptParsed | null {
  const record = coerceRecord(payload)
  if (!record) return null
  const replyTemplate = coerceReplyTemplate(record.replyTemplate)
  if (!replyTemplate) return null
  return {
    replyTemplate,
    badge: getReceiptBadge(replyTemplate.outcome),
    evidenceSections: coerceEvidenceSections(record.evidenceSections),
    actionRequestId: coerceString(record.actionRequestId),
    threadId: coerceString(record.threadId),
    toolRunId: coerceString(record.toolRunId),
    receiptId: coerceString(record.receiptId),
    toolName: coerceString(record.toolName),
    workflowFamily: coerceString(record.workflowFamily),
    terminalPhase: coerceString(record.terminalPhase),
    generatedAt: coerceString(record.generatedAt),
    errorCode: coerceString(record.errorCode),
  }
}

export function buildWorkflowReceiptSections(
  parsed: WorkflowReceiptParsed,
): ReceiptEvidenceSection[] {
  const overview: ReceiptEvidenceItem[] = [{ label: 'Status', value: parsed.badge.label }]
  if (parsed.terminalPhase) overview.push({ label: 'Terminal phase', value: parsed.terminalPhase })
  if (parsed.toolName) overview.push({ label: 'Tool', value: parsed.toolName })
  if (parsed.workflowFamily) overview.push({ label: 'Workflow family', value: parsed.workflowFamily })
  if (parsed.generatedAt) overview.push({ label: 'Generated at', value: parsed.generatedAt })

  const sections: ReceiptEvidenceSection[] = [{ title: 'Overview', items: overview }]
  // evidenceSections are already redacted at parse time.
  sections.push(...parsed.evidenceSections)
  const nextStep = parsed.replyTemplate.nextStep?.trim()
  if (nextStep) sections.push({ title: 'Next step', items: [{ label: 'Next', value: nextStep }] })
  return sections
}

// Plaintext used by the clipboard "Copy" and "Download" export paths. Built from
// the already-redacted sections so an export can never carry a secret.
export function buildWorkflowReceiptPlaintext(parsed: WorkflowReceiptParsed): string {
  const lines: string[] = [parsed.replyTemplate.title, `Status: ${parsed.badge.label}`]
  if (parsed.terminalPhase) lines.push(`Terminal phase: ${parsed.terminalPhase}`)
  lines.push('', parsed.replyTemplate.summary, '')
  for (const section of parsed.evidenceSections) {
    lines.push(`${section.title}:`)
    for (const item of section.items) lines.push(`- ${item.label}: ${item.value}`)
    lines.push('')
  }
  const nextStep = parsed.replyTemplate.nextStep?.trim()
  if (nextStep) lines.push(`Next step: ${nextStep}`, '')
  if (parsed.threadId) lines.push(`Thread ID: ${parsed.threadId}`)
  if (parsed.actionRequestId) lines.push(`Action ID: ${parsed.actionRequestId}`)
  if (parsed.toolRunId) lines.push(`Tool run ID: ${parsed.toolRunId}`)
  if (parsed.receiptId) lines.push(`Receipt ID: ${parsed.receiptId}`)
  if (parsed.errorCode) lines.push(`Error code: ${parsed.errorCode}`)
  return lines.join('\n').trim()
}
