'use client'

import {
  Badge,
  Button,
  ConfirmDialog,
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  StatusChip,
} from '@gio/bigsu-ui'
import { BigsuIcon } from '@gio/bigsu-icons'

import type { MutationResult } from '@/lib/admin/proxmox/use-proxmox-servers'
import type { ProxmoxServer, ValidateProxmoxServerResponse } from '@/lib/admin/proxmox/types'
import {
  deriveProxmoxValidationStatus,
  formatProxmoxRelativeTime,
  getProxmoxTlsBadge,
  getProxmoxTokenSecretBadge,
} from '@/lib/admin/proxmox/proxmox-status'

export type ServerDetailDrawerProps = {
  server: ProxmoxServer | null
  validateResult: ValidateProxmoxServerResponse | undefined
  validateBusy: boolean
  deleteBusy: boolean
  onCloseAction: () => void
  onEditAction: () => void
  onValidateAction: (serverId: string) => Promise<MutationResult>
  onDeleteAction: (serverId: string) => Promise<MutationResult>
}

// Contextual detail for one Proxmox server (issue #109). A Drawer inspects/edits
// alongside the list; a blocking decision (delete) escalates to ConfirmDialog.
// The drawer is controlled — open whenever a server is selected — and its inner
// content is keyed by server id so switching rows re-seeds. No secret is ever
// displayed; only presence (has_api_token_secret) and derived state. SSL
// verification is surfaced explicitly and accessibly as the only transport-
// security signal Proxmox exposes.
export function ServerDetailDrawer({ server, ...rest }: ServerDetailDrawerProps) {
  return (
    <Drawer open={server !== null} onOpenChange={(open) => (open ? undefined : rest.onCloseAction())}>
      {server ? <ServerDetailContent key={server.id} server={server} {...rest} /> : null}
    </Drawer>
  )
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex min-w-0 items-start justify-between gap-4">
      <dt className="shrink-0 text-text-secondary">{label}</dt>
      <dd className="min-w-0 break-all text-right text-text-primary">{value}</dd>
    </div>
  )
}

function ServerDetailContent({
  server,
  validateResult,
  validateBusy,
  deleteBusy,
  onEditAction,
  onValidateAction,
  onDeleteAction,
}: Omit<ServerDetailDrawerProps, 'onCloseAction'> & { server: ProxmoxServer }) {
  const tls = getProxmoxTlsBadge(server)
  const tokenSecret = getProxmoxTokenSecretBadge(server)

  const confirmDelete = async () => {
    const result = await onDeleteAction(server.id)
    if (!result.ok) throw new Error(result.message || 'delete failed')
  }

  return (
    <DrawerContent>
      <DrawerHeader>
        <DrawerTitle>{server.name}</DrawerTitle>
        <DrawerDescription>{server.base_url}</DrawerDescription>
      </DrawerHeader>

      <div className="flex flex-col gap-6">
        <section className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <StatusChip status={deriveProxmoxValidationStatus(validateResult)} />
            <Badge variant={tls.variant}>{tls.label}</Badge>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              loading={validateBusy}
              disabled={deleteBusy}
              onClick={() => void onValidateAction(server.id)}
            >
              Validate
            </Button>
            <Button variant="outline" disabled={deleteBusy} onClick={onEditAction}>
              <BigsuIcon name="edit" size="sm" aria-hidden />
              Edit
            </Button>
          </div>
        </section>

        <dl className="flex flex-col gap-2 text-sm">
          <DetailRow label="Server ID" value={<span className="font-mono">{server.id}</span>} />
          <DetailRow label="API token ID" value={server.api_token_id} />
          <DetailRow
            label="SSL verification"
            value={<Badge variant={tls.variant}>{tls.label}</Badge>}
          />
          <DetailRow
            label="API token secret"
            value={<Badge variant={tokenSecret.variant}>{tokenSecret.label}</Badge>}
          />
          <DetailRow label="Updated" value={formatProxmoxRelativeTime(server.updated_at)} />
          <DetailRow
            label="Latest validation"
            value={
              validateResult
                ? validateResult.message
                : 'Validate checks the Proxmox API connection using the stored token.'
            }
          />
        </dl>
      </div>

      <DrawerFooter>
        <ConfirmDialog
          tone="danger"
          title="Delete server?"
          description={`This permanently deletes the ${server.name} server configuration from NOA, including its stored API token secret. This cannot be undone.`}
          confirmLabel="Delete server"
          onConfirm={confirmDelete}
          trigger={
            <Button variant="destructive" disabled={validateBusy || deleteBusy}>
              Delete server
            </Button>
          }
        />
      </DrawerFooter>
    </DrawerContent>
  )
}
