'use client'

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

import type { MutationResult } from '@/lib/admin/roles/use-roles'
import { createRoleSchema, type CreateRoleValues } from '@/lib/admin/roles/role-schema'
import { submitOnEnter } from '@/lib/forms/submit-on-enter'

export type CreateRoleDialogProps = {
  open: boolean
  onOpenChangeAction: (open: boolean) => void
  onCreateAction: (name: string) => Promise<MutationResult>
}

// Create-role dialog (issue #107). A single FormField with a visible label, Zod
// validation through react-hook-form, and the field error linked via errorText.
// A duplicate name (ROLE_EXISTS) or invalid name comes back as the stable
// backend detail and is set on the field, so the input is preserved for
// correction rather than cleared. The form is keyed by `open` at the parent so
// each opening starts clean.
export function CreateRoleDialog({ open, onOpenChangeAction, onCreateAction }: CreateRoleDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChangeAction}>
      {open ? (
        <CreateRoleForm onOpenChangeAction={onOpenChangeAction} onCreateAction={onCreateAction} />
      ) : null}
    </Dialog>
  )
}

function CreateRoleForm({
  onOpenChangeAction,
  onCreateAction,
}: Omit<CreateRoleDialogProps, 'open'>) {
  const {
    control,
    handleSubmit,
    setError,
    formState: { isSubmitting },
  } = useForm<CreateRoleValues>({
    resolver: zodResolver(createRoleSchema),
    defaultValues: { name: '' },
  })

  const submit = handleSubmit(async ({ name }) => {
    const result = await onCreateAction(name)
    if (result.ok) {
      bigsuToast.success('Role created', { description: name })
      onOpenChangeAction(false)
      return
    }
    if (result.message) {
      setError('name', { type: 'server', message: result.message })
      bigsuToast.danger('Could not create role', { description: result.message })
    }
  })

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Add role</DialogTitle>
        <DialogDescription>
          Create a role name, then assign it tools and grant it to users.
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={submit} onKeyDown={submitOnEnter(submit)}>
        <Controller
          control={control}
          name="name"
          render={({ field, fieldState }) => (
            <FormField
              label="Role name"
              errorText={fieldState.error?.message}
              helperText="The API validates the name and its uniqueness."
            >
              <Input {...field} placeholder="e.g. support" autoComplete="off" />
            </FormField>
          )}
        />
      </form>

      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChangeAction(false)} disabled={isSubmitting}>
          Cancel
        </Button>
        {/* Click handler, not a submit button — sandbox-inheriting tabs refuse form submission. */}
        <Button type="button" onClick={() => void submit()} loading={isSubmitting}>
          Create role
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}
