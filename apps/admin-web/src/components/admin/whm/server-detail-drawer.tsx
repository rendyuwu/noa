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

import type { MutationResult } from '@/lib/admin/whm/use-whm-servers'
import type { ValidateWhmServerResponse, WhmServer } from '@/lib/admin/whm/types'
import {
  deriveWhmSshStatus,
  deriveWhmValidationStatus,
  formatWhmRelativeTime,
  getWhmFingerprintBadge,
  getWhmSshAuthLabel,
  getWhmTlsBadge,
} from '@/lib/admin/whm/whm-status'

import { ResellerTokensSection } from './reseller-tokens-section'

export type ServerDetailDrawerProps = {
  server: WhmServer | null
  validateResult: ValidateWhmServerResponse | undefined
  validateBusy: boolean
  deleteBusy: boolean
  onCloseAction: () => void
  onEditAction: () => void
  onValidateAction: (serverId: string) => Promise<MutationResult>
  onDeleteAction: (serverId: string) => Promise<MutationResult>
}

// Contextual detail for one WHM server (issue #108). A Drawer inspects/edits
// alongside the list; a blocking decision (delete) escalates to ConfirmDialog.
// The drawer is controlled — open whenever a server is selected — and its inner
// content is keyed by server id so switching rows re-seeds the reseller-token
// sub-controller. No secret is ever displayed; only presence/derived state.
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
}: Omit<ServerDetailDrawerProps, 'onCloseAction'> & { server: WhmServer }) {
  const tls = getWhmTlsBadge(server)
  const fingerprint = getWhmFingerprintBadge(server)

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
            <StatusChip status={deriveWhmValidationStatus(validateResult)} />
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
          <DetailRow label="API username" value={server.api_username} />
          <DetailRow label="Updated" value={formatWhmRelativeTime(server.updated_at)} />
          <DetailRow
            label="Latest validation"
            value={
              validateResult
                ? validateResult.message
                : 'Validate checks the WHM API token, then SSH (if configured), and pins the host key.'
            }
          />
        </dl>

        <section className="flex flex-col gap-3 border-t border-border-default pt-5">
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
              SSH access
            </span>
            <div className="flex items-center gap-2">
              <StatusChip status={deriveWhmSshStatus(server)} />
              <Badge variant={fingerprint.variant}>{fingerprint.label}</Badge>
            </div>
          </div>
          <dl className="flex flex-col gap-2 text-sm">
            <DetailRow label="SSH user" value={server.ssh_username || 'root (default)'} />
            <DetailRow label="SSH port" value={server.ssh_port ?? 22} />
            <DetailRow label="Authentication" value={getWhmSshAuthLabel(server)} />
            <DetailRow
              label="Host key fingerprint"
              value={
                <span className="font-mono text-xs">
                  {server.ssh_host_key_fingerprint ?? 'Not validated yet'}
                </span>
              }
            />
          </dl>
        </section>

        <ResellerTokensSection serverId={server.id} />
      </div>

      <DrawerFooter>
        <ConfirmDialog
          tone="danger"
          title="Delete server?"
          description={`This permanently deletes the ${server.name} server configuration from NOA, including its stored credentials and reseller tokens. This cannot be undone.`}
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
