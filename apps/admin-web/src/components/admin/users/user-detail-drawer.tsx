'use client'

import { useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Controller, useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { BigsuIcon } from '@gio/bigsu-icons'
import {
  Button,
  ConfirmDialog,
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  FormField,
  MultiSelect,
  StatusChip,
  bigsuToast,
} from '@gio/bigsu-ui'

import type { VerifiedUser } from '@/lib/auth/use-verified-auth'
import type { MutationResult } from '@/lib/admin/users/use-users'
import { roleAssignmentSchema, type RoleAssignmentValues } from '@/lib/admin/users/roles-schema'
import type { AdminUser } from '@/lib/admin/users/types'
import { coerceStringArray } from '@/lib/admin/users/users-api'
import {
  deriveUserStatus,
  formatRelativeTime,
  hasAdminRole,
  isActive,
  isAdminRole,
  isLastActiveAdmin,
  isSelf,
} from '@/lib/admin/users/user-status'

export type UserDetailDrawerProps = {
  user: AdminUser | null
  me: VerifiedUser
  allUsers: AdminUser[]
  availableRoles: string[]
  onCloseAction: () => void
  onSaveRolesAction: (userId: string, roles: string[]) => Promise<MutationResult>
  onSetActiveAction: (userId: string, isActive: boolean) => Promise<MutationResult>
  onDeleteAction: (userId: string) => Promise<MutationResult>
}

// Contextual detail + edit for one user (issue #106). BIGSU rule: a Drawer is for
// inspecting/editing alongside the list; a blocking decision (delete) escalates
// to ConfirmDialog. The drawer is controlled — open whenever a user is selected —
// and the inner content is keyed by user id so switching rows re-seeds the role
// form from that user's roles.
export function UserDetailDrawer({ user, ...rest }: UserDetailDrawerProps) {
  return (
    <Drawer open={user !== null} onOpenChange={(open) => (open ? undefined : rest.onCloseAction())}>
      {user ? <UserDetailContent key={user.id} user={user} {...rest} /> : null}
    </Drawer>
  )
}

function ToolsList({
  label,
  tools,
  description,
}: {
  label: string
  tools: string[]
  description?: string
}) {
  return (
    <section className="flex flex-col gap-2">
      <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
        {label}
      </span>
      {description ? <p className="text-sm text-text-secondary">{description}</p> : null}
      <div className="max-h-40 overflow-y-auto rounded-md border border-border-default bg-surface p-2">
        {tools.length === 0 ? (
          <p className="px-1 py-1 text-sm text-text-secondary">No tools granted.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {tools.map((tool) => (
              <li key={tool} className="rounded px-1 py-0.5 font-mono text-xs text-text-primary">
                {tool}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}

function UserDetailContent({
  user,
  me,
  allUsers,
  availableRoles,
  onSaveRolesAction,
  onSetActiveAction,
  onDeleteAction,
}: Omit<UserDetailDrawerProps, 'user' | 'onCloseAction'> & { user: AdminUser }) {
  const router = useRouter()
  const [statusBusy, setStatusBusy] = useState(false)

  const self = isSelf(user, me.id)
  const lastAdmin = isLastActiveAdmin(user, allUsers)
  const active = isActive(user)
  const status = deriveUserStatus(user)

  // Deactivating or deleting yourself, or the last active admin, is blocked by
  // the backend (SELF_DEACTIVATE_ADMIN / SELF_DELETE / LAST_ACTIVE_ADMIN). Mirror
  // it so the action is never offered as if it would succeed.
  const mutateBlocked = self || lastAdmin
  const blockedReason = self
    ? 'You cannot deactivate or delete your own account.'
    : 'This is the last active admin and cannot be deactivated or deleted.'

  // Lock the admin role for self / last-admin so it cannot be unchecked
  // (SELF_REMOVE_ADMIN_ROLE / LAST_ACTIVE_ADMIN). Every other option stays live.
  const lockAdminRole = (self || lastAdmin) && hasAdminRole(user)
  const roleOptions = useMemo(() => {
    const names = new Set<string>([...availableRoles, ...coerceStringArray(user.roles)])
    return Array.from(names)
      .sort((a, b) => a.localeCompare(b))
      .map((name) => ({ value: name, label: name, disabled: lockAdminRole && isAdminRole(name) }))
  }, [availableRoles, user.roles, lockAdminRole])

  const {
    control,
    handleSubmit,
    setError,
    formState: { isSubmitting, isDirty },
  } = useForm<RoleAssignmentValues>({
    resolver: zodResolver(roleAssignmentSchema),
    defaultValues: { roles: coerceStringArray(user.roles) },
  })

  // Save roles. On success the controller closes the drawer; on failure it stays
  // open with the form values preserved and the stable backend detail surfaced.
  const submitRoles = handleSubmit(async ({ roles }) => {
    const result = await onSaveRolesAction(user.id, roles)
    if (!result.current) return
    if (result.ok) {
      bigsuToast.success('Roles updated', { description: `Saved roles for ${user.email}.` })
    } else if (result.message) {
      // Link the stable backend detail to the field via FormField's errorText,
      // while also keeping a toast for global mutation feedback.
      setError('roles', { type: 'server', message: result.message })
      bigsuToast.danger('Could not save roles', { description: result.message })
    }
  })

  const toggleActive = async (rejectOnFailure = false) => {
    setStatusBusy(true)
    const result = await onSetActiveAction(user.id, !active)
    setStatusBusy(false)
    if (!result.current) return
    if (result.ok) {
      bigsuToast.success(active ? 'User deactivated' : 'User activated', {
        description: user.email,
      })
      return
    }
    if (result.message) {
      bigsuToast.danger('Could not update status', { description: result.message })
    }
    // ConfirmDialog stays open when its async onConfirm rejects, preserving the
    // blocking decision and stable server error for retry.
    if (rejectOnFailure) throw new Error(result.message || 'status update failed')
  }

  // ConfirmDialog keeps itself open when onConfirm rejects, so a failed delete
  // stays on screen for retry; a resolved one lets the controller close the
  // drawer once the row is gone.
  const confirmDelete = async () => {
    const result = await onDeleteAction(user.id)
    if (!result.current) return
    if (!result.ok) {
      if (result.message) {
        bigsuToast.danger('Could not delete user', { description: result.message })
      }
      throw new Error(result.message || 'delete failed')
    }
    bigsuToast.success('User deleted', { description: user.email })
  }

  return (
    <DrawerContent>
      <DrawerHeader>
        <DrawerTitle>{user.display_name ?? user.email}</DrawerTitle>
        <DrawerDescription>{user.email}</DrawerDescription>
      </DrawerHeader>

      <div className="flex flex-col gap-6">
        <section className="flex items-end justify-between gap-4">
          <div className="flex flex-col gap-2">
            <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
              Status
            </span>
            <StatusChip status={status} />
          </div>
          {active ? (
            <ConfirmDialog
              title="Deactivate user?"
              description={`${user.email} will immediately lose access to NOA until an administrator activates the account again.`}
              confirmLabel="Deactivate user"
              onConfirm={() => toggleActive(true)}
              trigger={
                <Button variant="outline" disabled={mutateBlocked}>
                  Deactivate
                </Button>
              }
            />
          ) : (
            <Button variant="outline" onClick={() => void toggleActive()} loading={statusBusy}>
              Activate
            </Button>
          )}
        </section>
        {active && mutateBlocked ? (
          <p className="-mt-4 text-xs text-text-secondary" role="note">
            {blockedReason}
          </p>
        ) : null}

        <dl className="flex flex-col gap-2 text-sm">
          <div className="flex min-w-0 items-start justify-between gap-4">
            <dt className="shrink-0 text-text-secondary">User ID</dt>
            <dd className="min-w-0 break-all text-right font-mono text-text-primary">{user.id}</dd>
          </div>
          <div className="flex items-center justify-between gap-4">
            <dt className="text-text-secondary">Last login</dt>
            <dd className="text-text-primary">{formatRelativeTime(user.last_login_at)}</dd>
          </div>
        </dl>

        {/*
          A link out, not a second list. Rendering the token table here
          would put a DataTable with its own loading/empty/error states and a
          mint dialog inside a 360px panel, and would give this drawer a second
          action competing with "Save roles". The tokens route owns all of that.
        */}
        <section className="flex items-center justify-between gap-4">
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
              MCP tokens
            </span>
            <p className="text-sm text-text-secondary">
              Credentials this user&rsquo;s LibreChat sessions authenticate with.
            </p>
          </div>
          <Button
            variant="outline"
            onClick={() => router.push(`/admin/users/${user.id}/tokens`)}
          >
            <BigsuIcon name="security" size="sm" aria-hidden />
            MCP tokens
          </Button>
        </section>

        <form onSubmit={submitRoles} className="flex flex-col gap-6">
          <Controller
            control={control}
            name="roles"
            render={({ field, fieldState }) => (
              <FormField
                label="Roles"
                errorText={fieldState.error?.message}
                helperText="Changes take effect immediately. The API validates role names and stays authoritative."
              >
                <MultiSelect
                  options={roleOptions}
                  value={field.value}
                  onValueChange={(roles) => {
                    // Selected disabled options still render removable chips in
                    // MultiSelect. Enforce the guard at the controlled boundary
                    // too, so self / last-admin cannot remove the admin role by
                    // clicking that chip's remove button.
                    if (lockAdminRole && !roles.some(isAdminRole)) return
                    field.onChange(roles)
                  }}
                  placeholder="Assign roles"
                />
              </FormField>
            )}
          />

          {/*
            One tool list, not two. The ported drawer also rendered "Legacy direct
            grants" from `direct_tools`, a field NOA's API does not send: direct per-user
            grants are a 410 and `AdminUserResponse` has no such key.
            A block that can only ever render empty is a capability the operator is
            invited to look for.
          */}
          <ToolsList label="Effective tools" tools={coerceStringArray(user.tools)} />
        </form>
      </div>

      <DrawerFooter>
        <ConfirmDialog
          tone="danger"
          title="Delete user?"
          description={`This permanently deletes ${user.email} and removes their access from NOA. This cannot be undone.`}
          confirmLabel="Delete user"
          onConfirm={confirmDelete}
          trigger={
            <Button variant="destructive" disabled={mutateBlocked}>
              Delete user
            </Button>
          }
        />
        {/* Click handler, not a submit button — sandbox-inheriting tabs refuse form submission. */}
        <Button
          type="button"
          onClick={() => void submitRoles()}
          loading={isSubmitting}
          disabled={!isDirty}
        >
          Save roles
        </Button>
      </DrawerFooter>
    </DrawerContent>
  )
}
