'use client'

import { useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  Button,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  FormField,
  Input,
  bigsuToast,
} from '@gio/bigsu-ui'

import type { MutationResult } from '@/lib/admin/whm/use-reseller-tokens'
import type { WhmServerToken } from '@/lib/admin/whm/types'
import { resellerTokenSchema, type ResellerTokenValues } from '@/lib/admin/whm/whm-schema'

export type TokenDialogMode = 'create' | 'rotate'

export type ResellerTokenDialogProps = {
  open: boolean
  mode: TokenDialogMode
  token: WhmServerToken | null
  onOpenChangeAction: (open: boolean) => void
  onCreateAction: (body: {
    owner_username: string
    api_username: string
    api_token: string
  }) => Promise<MutationResult>
  onRotateAction: (
    tokenId: string,
    body: { api_username: string; api_token?: string },
  ) => Promise<MutationResult>
}

// Add / rotate reseller-token dialog (issue #108). BIGSU form stack: react-hook-
// form + zodResolver, FormField per input. The token is write-only — it is NOT in
// the validated schema, lives only in local state that is thrown away when the
// dialog unmounts (the parent keys this by `open`), and is only sent in the
// request body. Owner is the routing key: editable on create, read-only on rotate
// so a rotate can never silently re-key routing.
export function ResellerTokenDialog({ open, mode, token, ...rest }: ResellerTokenDialogProps) {
  return (
    <Dialog open={open} onOpenChange={rest.onOpenChangeAction}>
      {open ? (
        <ResellerTokenForm key={`${mode}:${token?.id ?? 'new'}`} mode={mode} token={token} {...rest} />
      ) : null}
    </Dialog>
  )
}

function ResellerTokenForm({
  mode,
  token,
  onOpenChangeAction,
  onCreateAction,
  onRotateAction,
}: Omit<ResellerTokenDialogProps, 'open'>) {
  const isCreate = mode === 'create'
  // Write-only token value. Kept out of react-hook-form state entirely so it is
  // never serialized into form snapshots; discarded on unmount by the parent key.
  const [apiToken, setApiToken] = useState('')

  const {
    control,
    handleSubmit,
    setError,
    formState: { isSubmitting },
  } = useForm<ResellerTokenValues>({
    resolver: zodResolver(resellerTokenSchema),
    defaultValues: {
      ownerUsername: token?.owner_username ?? '',
      apiUsername: token?.api_username ?? '',
    },
  })

  const submit = handleSubmit(async (values) => {
    const trimmedToken = apiToken.trim()
    if (isCreate && !trimmedToken) {
      setError('apiUsername', { type: 'validate', message: 'API token is required' })
      return
    }

    const result = isCreate
      ? await onCreateAction({
          owner_username: values.ownerUsername.trim(),
          api_username: values.apiUsername.trim(),
          api_token: trimmedToken,
        })
      : await onRotateAction(token?.id ?? '', {
          api_username: values.apiUsername.trim(),
          // Only send the token when a new value was entered — a rename must not
          // rotate the stored secret.
          ...(trimmedToken ? { api_token: trimmedToken } : {}),
        })

    // Never retain the entered token, whatever the outcome.
    setApiToken('')

    if (result.ok) {
      bigsuToast.success(isCreate ? 'Reseller token added' : 'Reseller token updated', {
        description: values.ownerUsername.trim(),
      })
      onOpenChangeAction(false)
      return
    }
    if (result.message) {
      setError('ownerUsername', { type: 'server', message: result.message })
      bigsuToast.danger('Could not save reseller token', { description: result.message })
    }
  })

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{isCreate ? 'Add reseller token' : 'Rotate reseller token'}</DialogTitle>
        <DialogDescription>
          The token is stored encrypted and never displayed again. CHANGE actions for accounts owned
          by this reseller execute with this token.
        </DialogDescription>
      </DialogHeader>

      <form id="reseller-token-form" onSubmit={submit} className="flex flex-col gap-4">
        <Controller
          control={control}
          name="ownerUsername"
          render={({ field, fieldState }) => (
            <FormField
              label="Owner username"
              errorText={fieldState.error?.message}
              helperText={
                isCreate
                  ? 'The cPanel reseller/owner whose accounts this token writes to.'
                  : 'Owner is fixed for this token — create a new token to route a different owner.'
              }
            >
              <Input
                {...field}
                placeholder="reseller1"
                readOnly={!isCreate}
                disabled={isSubmitting}
                autoComplete="off"
              />
            </FormField>
          )}
        />
        <Controller
          control={control}
          name="apiUsername"
          render={({ field, fieldState }) => (
            <FormField label="API username" errorText={fieldState.error?.message}>
              <Input {...field} placeholder="reseller1" disabled={isSubmitting} autoComplete="off" />
            </FormField>
          )}
        />
        <FormField
          label="API token"
          helperText={isCreate ? undefined : 'Leave blank to keep the stored token.'}
        >
          <Input
            type="password"
            value={apiToken}
            onChange={(event) => setApiToken(event.target.value)}
            placeholder={isCreate ? '••••••••••' : 'Stored — enter a new token to rotate'}
            disabled={isSubmitting}
            autoComplete="new-password"
          />
        </FormField>
      </form>

      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChangeAction(false)} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" form="reseller-token-form" loading={isSubmitting}>
          {isCreate ? 'Add token' : 'Save token'}
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}
