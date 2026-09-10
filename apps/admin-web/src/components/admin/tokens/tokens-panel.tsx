'use client'

import { useState } from 'react'
import { Button, ConfirmDialog, DataTable, bigsuToast } from '@gio/bigsu-ui'
import { BigsuIcon } from '@gio/bigsu-icons'

import { useTokens } from '@/lib/admin/tokens/use-tokens'
import type { McpToken, TokenScope } from '@/lib/admin/tokens/types'

import { MintTokenDialog } from './mint-token-dialog'
import { tokenColumns } from './token-columns'

export type TokensPanelProps = {
  scope: TokenScope
}

// One panel, parameterised by scope — mirroring why the backend keeps both
// routers in one module over one response model (mcp_tokens.py:9-14). The
// scope is threaded straight to the controller, which hands it to the single
// place a token path is built; this component never assembles a URL.
//
// The mint Button sits in the toolbar row beside the table, not in PageHeader's
// `primaryAction` slot. That is a deliberate, consistent house deviation from
// BIGSU's stated page order — no page in this app uses `primaryAction` — and it
// is what lets one component own both the button and the dialog it opens.
export function TokensPanel({ scope }: TokensPanelProps) {
  const { tokens, loading, loadError, reload, mint, revoke } = useTokens(scope)

  const [mintOpen, setMintOpen] = useState(false)
  const [revokeTarget, setRevokeTarget] = useState<McpToken | null>(null)

  const owner = scope.kind === 'self' ? 'you' : 'this user'

  // ConfirmDialog closes on resolution and stays open when the promise rejects,
  // so a failed revoke keeps the blocking decision on screen with the server's
  // wording available for retry. The 404 case is not special-cased here on
  // purpose: the controller re-reads the list and the server decides whether the
  // row still exists — the UI must not resolve whether it saw a token that was
  // already gone or one that was never this operator's (mcp_tokens.py:191-197).
  const confirmRevoke = async () => {
    const target = revokeTarget
    if (!target) return

    const outcome = await revoke(target.id)
    if ('redirecting' in outcome) return

    if (!outcome.ok) {
      bigsuToast.danger('Could not revoke the token', { description: outcome.message })
      throw new Error(outcome.message || 'revoke failed')
    }

    bigsuToast.success('Token revoked', { description: target.token_prefix })
    setRevokeTarget(null)
  }

  return (
    <div className="mt-6 flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-end gap-3">
        <Button variant="secondary" onClick={() => void reload()} disabled={loading}>
          <BigsuIcon name="refresh" size="sm" aria-hidden />
          Refresh
        </Button>
        <Button onClick={() => setMintOpen(true)}>
          <BigsuIcon name="create" size="sm" aria-hidden />
          Mint token
        </Button>
      </div>

      <DataTable
        columns={tokenColumns}
        data={tokens}
        getRowId={(token) => token.id}
        loading={loading}
        searchable
        error={loadError ? { message: loadError, onRetry: () => void reload() } : undefined}
        pagination={{ pageSize: 10 }}
        rowActions={(token) => [
          {
            label: 'Revoke token',
            icon: 'delete',
            destructive: true,
            // The dropdown closes itself on select, which would take an
            // uncontrolled ConfirmDialog trigger down with it. Record the target
            // instead and let the controlled dialog below open on the next
            // render, outside the menu's subtree.
            onSelect: () => setRevokeTarget(token),
          },
        ]}
        emptyState={{
          title: 'No MCP tokens yet',
          description: `Mint a token to let LibreChat authenticate to NOA as ${owner}.`,
          action: (
            <Button size="sm" variant="secondary" onClick={() => setMintOpen(true)}>
              Mint token
            </Button>
          ),
        }}
      />

      <MintTokenDialog open={mintOpen} onOpenChangeAction={setMintOpen} onMintAction={mint} />

      <ConfirmDialog
        open={revokeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRevokeTarget(null)
        }}
        tone="danger"
        title="Revoke this token?"
        description={
          revokeTarget
            ? `${revokeTarget.token_prefix} stops authenticating immediately. Any LibreChat session using it loses access to NOA until a new token is minted. This cannot be undone.`
            : 'This token stops authenticating immediately. This cannot be undone.'
        }
        confirmLabel="Revoke token"
        onConfirm={confirmRevoke}
      />
    </div>
  )
}
