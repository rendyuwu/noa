'use client'

import type { UseFormReturn } from 'react-hook-form'
import { Checkbox, FormField, Input, RadioGroup, RadioItem, Textarea } from '@gio/bigsu-ui'

import type { WhmServer } from '@/lib/admin/whm/types'
import type { WhmServerFormState } from '@/lib/admin/whm/whm-form'

// The WHM half of the create/edit server dialog (issue #108) — the field
// sections that genuinely differ between verticals. The shell around them
// (dialog, resolver, submit, toasts) is components/admin/servers.
// WHM is the only vertical whose SSH access is optional, hence the toggle.

type FieldsProps = {
  form: UseFormReturn<WhmServerFormState>
  mode: 'create' | 'update'
  existingServer: WhmServer | null
  busy: boolean
}

export function WhmServerFields({ form, mode, existingServer, busy }: FieldsProps) {
  return (
    <>
      <ServerApiFields form={form} mode={mode} busy={busy} />
      <ServerSshFields form={form} mode={mode} existingServer={existingServer} busy={busy} />
    </>
  )
}

function ServerApiFields({
  form,
  mode,
  busy,
}: {
  form: UseFormReturn<WhmServerFormState>
  mode: 'create' | 'update'
  busy: boolean
}) {
  const { register, watch, setValue, formState } = form
  const errors = formState.errors
  return (
    <section className="flex flex-col gap-4">
      <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">WHM API</span>
      <FormField label="Name" errorText={errors.name?.message}>
        <Input {...register('name')} placeholder="web1" disabled={busy} autoComplete="off" />
      </FormField>
      <FormField label="Base URL" errorText={errors.baseUrl?.message}>
        <Input
          {...register('baseUrl')}
          placeholder="https://whm.example.com:2087"
          disabled={busy}
          autoComplete="off"
        />
      </FormField>
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField label="API username" errorText={errors.apiUsername?.message}>
          <Input {...register('apiUsername')} placeholder="root" disabled={busy} autoComplete="off" />
        </FormField>
        <div className="flex items-end pb-2">
          <Checkbox
            label="Verify SSL"
            checked={watch('verifySsl')}
            onCheckedChange={(checked) => setValue('verifySsl', checked === true)}
            disabled={busy}
          />
        </div>
      </div>
      <FormField
        label="API token"
        errorText={errors.apiToken?.message}
        helperText={
          mode === 'update' ? 'Leave blank to keep the stored API token.' : undefined
        }
      >
        <Input
          {...register('apiToken')}
          type="password"
          placeholder={mode === 'create' ? '••••••••••' : 'Stored — enter a new token to replace'}
          disabled={busy}
          autoComplete="new-password"
        />
      </FormField>

      <div className="flex flex-col gap-2">
        <Checkbox
          label="Reseller credential"
          checked={watch('isResellerCredential')}
          onCheckedChange={(checked) => setValue('isResellerCredential', checked === true)}
          disabled={busy}
        />
        <p className="text-sm text-text-secondary">
          Check this only if the API username above belongs to a reseller, not root. WHM only
          lets a reseller credential change accounts it owns, so it can be resolved back to this
          row only by name — Name must match API username exactly (case-insensitive), or saving
          is refused.
        </p>
      </div>
    </section>
  )
}

function ServerSshFields({
  form,
  mode,
  existingServer,
  busy,
}: {
  form: UseFormReturn<WhmServerFormState>
  mode: 'create' | 'update'
  existingServer: WhmServer | null
  busy: boolean
}) {
  const { register, watch, setValue, formState } = form
  const errors = formState.errors
  const enableSsh = watch('enableSsh')
  const authMode = watch('sshAuthMode')

  return (
    <section className="flex flex-col gap-4 border-t border-border-default pt-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
            SSH access
          </span>
          <p className="text-sm text-text-secondary">
            Used for CSF, firewall, and other SSH-backed server tools.
          </p>
        </div>
        <Checkbox
          label="Enable SSH"
          checked={enableSsh}
          onCheckedChange={(checked) => setValue('enableSsh', checked === true)}
          disabled={busy}
        />
      </div>

      {enableSsh ? (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-text-secondary">
            {mode === 'update'
              ? 'Leave secret fields blank to keep the stored values. Enter a new value to replace them.'
              : 'SSH is optional, but required for CSF and other SSH-backed tools.'}
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="SSH username" helperText="Leave blank to fall back to root.">
              <Input
                {...register('sshUsername')}
                placeholder={existingServer?.ssh_username ?? 'root (default)'}
                disabled={busy}
                autoComplete="off"
              />
            </FormField>
            <FormField
              label="SSH port"
              errorText={errors.sshPort?.message}
              helperText="Leave blank to use port 22."
            >
              <Input
                {...register('sshPort')}
                type="number"
                min={1}
                max={65535}
                placeholder={existingServer?.ssh_port != null ? String(existingServer.ssh_port) : '22'}
                disabled={busy}
              />
            </FormField>
          </div>

          <FormField label="Authentication">
            <RadioGroup
              aria-label="Authentication"
              value={authMode}
              onValueChange={(value) =>
                setValue('sshAuthMode', value === 'password' ? 'password' : 'private_key')
              }
            >
              <RadioItem value="private_key" label="SSH key" disabled={busy} />
              <RadioItem value="password" label="Password" disabled={busy} />
            </RadioGroup>
          </FormField>

          {authMode === 'password' ? (
            <FormField label="SSH password" errorText={errors.sshPassword?.message}>
              <Input
                {...register('sshPassword')}
                type="password"
                placeholder={
                  mode === 'create' ? '••••••••••' : 'Stored — enter a new password to replace'
                }
                disabled={busy}
                autoComplete="new-password"
              />
            </FormField>
          ) : (
            <>
              <FormField label="SSH private key" errorText={errors.sshPrivateKey?.message}>
                <Textarea
                  {...register('sshPrivateKey')}
                  className="min-h-32 font-mono text-xs"
                  placeholder={
                    mode === 'create'
                      ? '-----BEGIN OPENSSH PRIVATE KEY-----'
                      : 'Stored — paste a new private key to replace'
                  }
                  disabled={busy}
                />
              </FormField>
              <FormField
                label="Key passphrase"
                helperText="Optional. If you replace the private key and leave this blank, the stored passphrase is cleared."
              >
                <Input
                  {...register('sshPrivateKeyPassphrase')}
                  type="password"
                  placeholder={
                    mode === 'create' ? 'Optional' : 'Stored — enter a new passphrase to replace'
                  }
                  disabled={busy}
                  autoComplete="new-password"
                />
              </FormField>
            </>
          )}
        </div>
      ) : (
        <p className="text-sm text-text-secondary">
          Not configured. CSF, firewall, and other SSH-backed tools are unavailable until you add
          SSH credentials and run Validate.
        </p>
      )}
    </section>
  )
}
