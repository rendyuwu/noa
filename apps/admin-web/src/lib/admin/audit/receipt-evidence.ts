// Evidence shapes carried by a workflow receipt (§T.48).
//
// Ported from the old repo's `lib/assistant/protocol.ts`, which held the whole
// chat wire contract. Chat is not this app's surface, so only the two shapes the
// receipt actually renders come across, and they drop the `Assistant` prefix
// their old home gave them: an evidence section here belongs to an action
// receipt in the audit log, not to a conversation.
//
// Pure data. No imports from any component module — the receipt parser redacts
// at this layer (see `workflow-receipt.ts`), so nothing about presentation may
// leak in and give a second, unredacted path to the same values.

export type ReceiptEvidenceItem = {
  label: string
  value: string
}

export type ReceiptEvidenceSection = {
  title: string
  items: ReceiptEvidenceItem[]
}
