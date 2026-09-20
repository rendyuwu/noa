'use client'

import { Badge } from '@gio/bigsu-ui'

import { ServersPage } from '@/components/admin/servers/servers-page'
import type { ServerVertical } from '@/components/admin/servers/types'
import {
  EMPTY_PROXMOX_FORM,
  buildProxmoxCreatePayload,
  buildProxmoxUpdatePayload,
  proxmoxFormStateFromServer,
  validateProxmoxServerForm,
  type ProxmoxServerFormState,
} from '@/lib/admin/proxmox/proxmox-form'
import { getProxmoxTokenSecretBadge } from '@/lib/admin/proxmox/proxmox-status'
import type { ProxmoxServer, ValidateProxmoxServerResponse } from '@/lib/admin/proxmox/types'
import { useProxmoxServers } from '@/lib/admin/proxmox/use-proxmox-servers'
import { formatRelativeTime } from '@/lib/admin/shared/relative-time'
import { getTlsBadge } from '@/lib/admin/shared/server-status'

import { ProxmoxServerFields } from './server-form-fields'

// The Proxmox servers administration page (issue #109), expressed as a config
// over the shared admin servers UI (components/admin/servers). Everything
// structural — header, filter, table, drawer, dialog, race guards — is there;
// what is here is only what Proxmox does differently.
const PROXMOX_VERTICAL: ServerVertical<
  ProxmoxServer,
  ValidateProxmoxServerResponse,
  ProxmoxServerFormState
> = {
  useServers: useProxmoxServers,

  title: 'Proxmox servers',
  description:
    'Manage Proxmox API credentials, SSL verification, and connection validation. The API token secret is stored encrypted and never displayed after save.',
  searchPlaceholder: 'Search by name, URL, or ID',
  emptyTitle: 'No Proxmox servers yet',
  emptyDescription: 'Add a Proxmox server to manage virtualization infrastructure.',
  searchFields: (server) => [server.base_url],

  rowSubtitle: (server) => server.base_url,
  // Proxmox has no SSH host key, so verify_ssl is its only transport-security
  // signal; stored-token presence rides beside it.
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
      id: 'token',
      header: 'Token secret',
      cell: ({ row }) => {
        const token = getProxmoxTokenSecretBadge(row.original)
        return <Badge variant={token.variant}>{token.label}</Badge>
      },
    },
  ],

  detail: (server, validateResult) => {
    const tls = getTlsBadge(server)
    const tokenSecret = getProxmoxTokenSecretBadge(server)
    return {
      subtitle: server.base_url,
      badges: [tls],
      rows: [
        { label: 'API token ID', value: server.api_token_id },
        {
          label: 'SSL verification',
          value: <Badge variant={tls.variant}>{tls.label}</Badge>,
        },
        {
          label: 'API token secret',
          value: <Badge variant={tokenSecret.variant}>{tokenSecret.label}</Badge>,
        },
        { label: 'Updated', value: formatRelativeTime(server.updated_at, '—') },
        {
          label: 'Latest validation',
          value: validateResult
            ? validateResult.message
            : 'Validate checks the Proxmox API connection using the stored token.',
        },
      ],
      sections: [],
      deleteDescription: `This permanently deletes the ${server.name} server configuration from NOA, including its stored API token secret. This cannot be undone.`,
    }
  },

  noun: 'Proxmox server',
  formDescription: (mode) =>
    mode === 'create'
      ? 'The Proxmox API token secret is stored encrypted and never displayed again.'
      : 'Stored secrets can be replaced, but they are never shown again. Leave the secret blank to keep the stored value.',
  formDefaults: (mode, existingServer) =>
    existingServer && mode !== 'create'
      ? proxmoxFormStateFromServer(existingServer)
      : EMPTY_PROXMOX_FORM,
  validateForm: validateProxmoxServerForm,
  buildPayload: (values, mode, existingServer) =>
    mode === 'create' || !existingServer
      ? buildProxmoxCreatePayload(values)
      : buildProxmoxUpdatePayload(values),
  // The API token secret must not persist after a submit: blanked here whatever
  // the outcome, so it never lingers in form state (success closes the dialog; a
  // failure keeps the non-secret input for correction but drops the secret).
  afterSubmit: (form) => form.setValue('apiTokenSecret', ''),
  renderFields: ({ form, mode, busy }) => (
    <ProxmoxServerFields form={form} mode={mode} busy={busy} />
  ),
}

export function ProxmoxServersPage() {
  return <ServersPage vertical={PROXMOX_VERTICAL} />
}
