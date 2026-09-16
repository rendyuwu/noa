'use client'

import { useForm, type UseFormReturn } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  FormField,
  Input,
  RadioGroup,
  RadioItem,
  Textarea,
  bigsuToast,
} from '@gio/bigsu-ui'

import type { MutationResult } from '@/lib/admin/whm/use-whm-servers'
import type { WhmServer } from '@/lib/admin/whm/types'
import {
  EMPTY_WHM_FORM,
  buildWhmCreatePayload,
  buildWhmUpdatePayload,
  whmFormStateFromServer,
  type WhmServerFormState,
} from '@/lib/admin/whm/whm-form'
import { buildWhmServerFormSchema } from '@/lib/admin/whm/whm-schema'
import { submitOnEnter } from '@/lib/forms/submit-on-enter'

export type ServerFormDialogProps = {
  open: boolean
  mode: 'create' | 'update'
  existingServer: WhmServer | null
  onOpenChangeAction: (open: boolean) => void
  onSubmitAction: (body: Record<string, unknown>) => Promise<MutationResult>
}

// Create/edit WHM server dialog (issue #108). BIGSU form stack: react-hook-form
// + zodResolver, every field wrapped in FormField with a visible label and error
// wiring. The form is keyed by `open`+server id at the parent so each opening
// re-seeds from the server and every secret field starts blank. Secrets are
// write-only: never seeded, cleared on unmount by the re-key, and never retained
// after submit (the dialog closes on success; on failure the non-secret input is
// preserved and the stable backend detail is surfaced).
export function ServerFormDialog({ open, mode, existingServer, ...rest }: ServerFormDialogProps) {
  return (
    <Dialog open={open} onOpenChange={rest.onOpenChangeAction}>
      {open ? (
        <ServerForm
          key={`${mode}:${existingServer?.id ?? 'new'}`}
          mode={mode}
          existingServer={existingServer}
          {...rest}
        />
      ) : null}
    </Dialog>
  )
}

function ServerForm({
  mode,
  existingServer,
  onOpenChangeAction,
  onSubmitAction,
}: Omit<ServerFormDialogProps, 'open'>) {
  const form = useForm<WhmServerFormState>({
    resolver: zodResolver(buildWhmServerFormSchema(mode, existingServer)),
    defaultValues: existingServer ? whmFormStateFromServer(existingServer) : EMPTY_WHM_FORM,
  })
  const isCreate = mode === 'create'

  const submit = form.handleSubmit(async (values) => {
    const body =
      isCreate || !existingServer
        ? buildWhmCreatePayload(values)
        : buildWhmUpdatePayload(values, existingServer)
    const result = await onSubmitAction(body)
    if (result.ok) {
      bigsuToast.success(isCreate ? 'WHM server added' : 'WHM server saved', {
        description: values.name.trim(),
      })
      onOpenChangeAction(false)
      return
    }
    if (result.message) {
      form.setError('name', { type: 'server', message: result.message })
      bigsuToast.danger(isCreate ? 'Could not add server' : 'Could not save server', {
        description: result.message,
      })
    }
  })

  const busy = form.formState.isSubmitting

  return (
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>{isCreate ? 'Add WHM server' : 'Edit WHM server'}</DialogTitle>
        <DialogDescription>
          {isCreate
            ? 'WHM API token and SSH credentials are stored encrypted and never displayed again. CSF and firewall tools use the SSH path.'
            : 'Stored secrets can be replaced, but they are never shown again. Leave a secret blank to keep the stored value.'}
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={submit} onKeyDown={submitOnEnter(submit)} className="flex flex-col gap-5">
        <ServerApiFields form={form} mode={mode} busy={busy} />
        <ServerSshFields form={form} mode={mode} existingServer={existingServer} busy={busy} />
      </form>

      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChangeAction(false)} disabled={busy}>
          Cancel
        </Button>
        {/* Click handler, not a submit button — sandbox-inheriting tabs refuse form submission. */}
        <Button type="button" onClick={() => void submit()} loading={busy}>
          {isCreate ? 'Save' : 'Save changes'}
        </Button>
      </DialogFooter>
    </DialogContent>
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
