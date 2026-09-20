'use client'

import { Badge, StatusChip } from '@gio/bigsu-ui'

import { ServersPage } from '@/components/admin/servers/servers-page'
import type { ServerVertical } from '@/components/admin/servers/types'
import { formatRelativeTime } from '@/lib/admin/shared/relative-time'
import {
  deriveSshStatus,
  getFingerprintBadge,
  getSshAuthLabel,
  getTlsBadge,
} from '@/lib/admin/shared/server-status'
import type { ValidateWhmServerResponse, WhmServer } from '@/lib/admin/whm/types'
import { useWhmServers } from '@/lib/admin/whm/use-whm-servers'
import {
  EMPTY_WHM_FORM,
  buildWhmCreatePayload,
  buildWhmUpdatePayload,
  validateWhmServerForm,
  whmFormStateFromServer,
  type WhmServerFormState,
} from '@/lib/admin/whm/whm-form'
import { getWhmResellerBadge, getWhmValidationMessage } from '@/lib/admin/whm/whm-status'

import { WhmServerFields } from './server-form-fields'

// The WHM servers administration page (issue #108), expressed as a config over
// the shared admin servers UI (components/admin/servers). Everything structural
// — header, filter, table, drawer, dialog, race guards — is there; what is here
// is only what WHM does differently.
const WHM_VERTICAL: ServerVertical<WhmServer, ValidateWhmServerResponse, WhmServerFormState> = {
  useServers: useWhmServers,

  title: 'WHM servers',
  description:
    'Manage WHM API and SSH credentials and validate connectivity. Secrets are stored encrypted and never displayed after save.',
  searchPlaceholder: 'Search by name, URL, or ID',
  emptyTitle: 'No WHM servers yet',
  emptyDescription: 'Add a WHM server to manage hosting infrastructure.',
  searchFields: (server) => [server.base_url],

  rowSubtitle: (server) => server.base_url,
  // The reseller flag rides in the Server cell, shown only on `true` rows: an
  // operator scanning the list should not have to open every row to tell a
  // scoped credential from root.
  rowBadge: getWhmResellerBadge,
  // WHM speaks both HTTPS and SSH, so it carries both transport-security
  // signals: TLS verification and SSH credential state.
  extraColumns: [
    {
      id: 'tls',
      header: 'TLS',
      cell: ({ row }) => {
        const tls = getTlsBadge(row.original)
        return <Badge variant={tls.variant}>{tls.label}</Badge>
      },
    },
    {
      id: 'ssh',
      header: 'SSH',
      cell: ({ row }) => <StatusChip status={deriveSshStatus(row.original)} />,
    },
  ],

  detail: (server, validateResult) => {
    const tls = getTlsBadge(server)
    const reseller = getWhmResellerBadge(server)
    return {
      subtitle: server.base_url,
      badges: reseller ? [tls, reseller] : [tls],
      rows: [
        { label: 'API username', value: server.api_username },
        { label: 'Updated', value: formatRelativeTime(server.updated_at, '—') },
        { label: 'Latest validation', value: getWhmValidationMessage(validateResult) },
      ],
      sections: [
        {
          title: 'SSH access',
          status: deriveSshStatus(server),
          badges: [getFingerprintBadge(server)],
          rows: [
            { label: 'SSH user', value: server.ssh_username || 'root (default)' },
            { label: 'SSH port', value: server.ssh_port ?? 22 },
            { label: 'Authentication', value: getSshAuthLabel(server) },
            {
              label: 'Host key fingerprint',
              value: (
                <span className="font-mono text-xs">
                  {server.ssh_host_key_fingerprint ?? 'Not validated yet'}
                </span>
              ),
            },
          ],
        },
      ],
      deleteDescription: `This permanently deletes the ${server.name} server configuration from NOA, including its stored credentials. This cannot be undone.`,
    }
  },

  noun: 'WHM server',
  formDescription: (mode) =>
    mode === 'create'
      ? 'WHM API token and SSH credentials are stored encrypted and never displayed again. CSF and firewall tools use the SSH path.'
      : 'Stored secrets can be replaced, but they are never shown again. Leave a secret blank to keep the stored value.',
  formDefaults: (_mode, existingServer) =>
    existingServer ? whmFormStateFromServer(existingServer) : EMPTY_WHM_FORM,
  validateForm: validateWhmServerForm,
  buildPayload: (values, mode, existingServer) =>
    mode === 'create' || !existingServer
      ? buildWhmCreatePayload(values)
      : buildWhmUpdatePayload(values, existingServer),
  renderFields: ({ form, mode, existingServer, busy }) => (
    <WhmServerFields form={form} mode={mode} existingServer={existingServer} busy={busy} />
  ),
}

export function WhmServersPage() {
  return <ServersPage vertical={WHM_VERTICAL} />
}
