'use client'

import { useEffect, useState } from 'react'
import { Button, ConfirmDialog, StatusChip } from '@gio/bigsu-ui'
import { BigsuIcon } from '@gio/bigsu-icons'

import { useResellerTokens } from '@/lib/admin/whm/use-reseller-tokens'
import type { WhmServerToken } from '@/lib/admin/whm/types'
import { deriveWhmValidationStatus, formatWhmRelativeTime } from '@/lib/admin/whm/whm-status'

import { ResellerTokenDialog, type TokenDialogMode } from './reseller-token-dialog'

// Per-reseller token sub-section (issue #108), rendered inside the server detail
// drawer. Tokens lazy-load on first expand and are not refetched on re-expand;
// the token value is write-only and never rendered. Create/rotate go through the
// dialog; validate and delete are inline, with delete escalating to ConfirmDialog.
export function ResellerTokensSection({ serverId }: { serverId: string }) {
  const [expanded, setExpanded] = useState(false)
  const controller = useResellerTokens(serverId)
  const {
    tokens,
    loading,
    loadError,
    load,
    reload,
    validateResultById,
    validateBusyId,
    deleteBusyId,
    createToken,
    rotateToken,
    deleteToken,
    validateToken,
  } = controller

  const [dialogOpen, setDialogOpen] = useState(false)
  const [dialogMode, setDialogMode] = useState<TokenDialogMode>('create')
  const [activeToken, setActiveToken] = useState<WhmServerToken | null>(null)

  useEffect(() => {
    if (expanded) void load()
  }, [expanded, load])

  const openCreate = () => {
    setDialogMode('create')
    setActiveToken(null)
    setDialogOpen(true)
  }
  const openRotate = (token: WhmServerToken) => {
    setDialogMode('rotate')
    setActiveToken(token)
    setDialogOpen(true)
  }

  return (
    <section className="flex flex-col gap-3 border-t border-border-default pt-5">
      <div className="flex items-start justify-between gap-3">
        <button
          type="button"
          className="flex min-w-0 flex-col items-start gap-1 rounded text-left outline-none focus-visible:ring-2 focus-visible:ring-border-focus"
          onClick={() => setExpanded((prev) => !prev)}
          aria-expanded={expanded}
          aria-label="Reseller tokens"
        >
          <span className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-text-secondary">
            Reseller tokens
            <BigsuIcon name={expanded ? 'chevronDown' : 'chevronRight'} size="xs" aria-hidden />
          </span>
          <span className="text-sm text-text-secondary">
            Per-owner WHM API tokens. Account CHANGE actions route to the owning reseller&apos;s
            token; reads stay on the root token. Optional for single-owner servers.
          </span>
        </button>
        {expanded ? (
          <Button size="sm" variant="secondary" className="shrink-0" onClick={openCreate}>
            <BigsuIcon name="create" size="sm" aria-hidden />
            Add token
          </Button>
        ) : null}
      </div>

      {expanded ? (
        <div className="flex flex-col gap-2">
          {loading ? (
            <p className="text-sm text-text-secondary">Loading reseller tokens…</p>
          ) : loadError ? (
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm text-status-danger" role="alert">
                {loadError}
              </p>
              <Button size="sm" variant="outline" onClick={() => void reload()}>
                Try again
              </Button>
            </div>
          ) : tokens.length === 0 ? (
            <p className="text-sm text-text-secondary">
              No reseller tokens. The root token handles every account it owns; add a token for each
              reseller whose accounts NOA must modify.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {tokens.map((token) => (
                <TokenRow
                  key={token.id}
                  token={token}
                  status={deriveWhmValidationStatus(validateResultById[token.id])}
                  failure={
                    validateResultById[token.id]?.ok === false
                      ? (validateResultById[token.id]?.message ?? null)
                      : null
                  }
                  validating={validateBusyId === token.id}
                  deleting={deleteBusyId === token.id}
                  onValidate={() => void validateToken(token.id)}
                  onRotate={() => openRotate(token)}
                  onDelete={async () => {
                    const result = await deleteToken(token.id)
                    if (!result.ok) throw new Error(result.message || 'delete failed')
                  }}
                />
              ))}
            </ul>
          )}
        </div>
      ) : null}

      <ResellerTokenDialog
        open={dialogOpen}
        mode={dialogMode}
        token={activeToken}
        onOpenChangeAction={setDialogOpen}
        onCreateAction={createToken}
        onRotateAction={rotateToken}
      />
    </section>
  )
}

function TokenRow({
  token,
  status,
  failure,
  validating,
  deleting,
  onValidate,
  onRotate,
  onDelete,
}: {
  token: WhmServerToken
  status: ReturnType<typeof deriveWhmValidationStatus>
  failure: string | null
  validating: boolean
  deleting: boolean
  onValidate: () => void
  onRotate: () => void
  onDelete: () => Promise<void>
}) {
  const busy = validating || deleting
  return (
    <li className="flex flex-col gap-2 rounded-md border border-border-default bg-surface p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-text-primary">{token.owner_username}</span>
            <StatusChip status={status} />
          </div>
          <span className="text-xs text-text-secondary">API user: {token.api_username}</span>
          <span className="text-xs text-text-secondary">
            Updated {formatWhmRelativeTime(token.updated_at)}
          </span>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            loading={validating}
            disabled={busy}
            aria-label={`Validate token ${token.owner_username}`}
            onClick={onValidate}
          >
            Validate
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            aria-label={`Rotate token ${token.owner_username}`}
            onClick={onRotate}
          >
            Rotate
          </Button>
          <ConfirmDialog
            tone="danger"
            title="Delete reseller token?"
            description={`This removes the stored token for ${token.owner_username}. Account CHANGE actions for accounts owned by ${token.owner_username} will fail with no_token_for_owner until a new token is added.`}
            confirmLabel="Delete token"
            onConfirm={onDelete}
            trigger={
              <Button
                size="sm"
                variant="destructive"
                disabled={busy}
                aria-label={`Delete token ${token.owner_username}`}
              >
                Delete
              </Button>
            }
          />
        </div>
      </div>
      {failure ? (
        <p className="text-xs text-status-danger">{failure}</p>
      ) : null}
    </li>
  )
}
