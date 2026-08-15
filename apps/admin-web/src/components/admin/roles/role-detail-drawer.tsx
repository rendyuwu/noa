'use client'

import { useMemo, useState } from 'react'
import { Controller, useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  Button,
  Checkbox,
  ConfirmDialog,
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  FormField,
  Input,
  bigsuToast,
} from '@gio/bigsu-ui'

import { coerceStringArray } from '@/lib/admin/shared/coerce'
import type { MutationResult } from '@/lib/admin/roles/use-roles'
import { roleToolsSchema, type RoleToolsValues } from '@/lib/admin/roles/role-schema'

export type RoleDetailDrawerProps = {
  role: string | null
  availableTools: string[]
  roleTools: string[]
  roleToolsLoading: boolean
  roleToolsError: string | null
  onCloseAction: () => void
  onSaveToolsAction: (name: string, tools: string[]) => Promise<MutationResult>
  onDeleteAction: (name: string) => Promise<MutationResult>
}

// Contextual detail + edit for one role (issue #107). BIGSU rule: a Drawer is for
// inspecting/editing alongside the list; the blocking decision (delete) escalates
// to ConfirmDialog. The drawer is controlled — open whenever a role is selected —
// and the inner content is keyed by role name so switching rows re-seeds the
// allowlist form from that role's tools.
export function RoleDetailDrawer({ role, ...rest }: RoleDetailDrawerProps) {
  return (
    <Drawer open={role !== null} onOpenChange={(open) => (open ? undefined : rest.onCloseAction())}>
      {role ? <RoleDetailContent key={role} role={role} {...rest} /> : null}
    </Drawer>
  )
}

function RoleDetailContent({
  role,
  availableTools,
  roleTools,
  roleToolsLoading,
  roleToolsError,
  onSaveToolsAction,
  onDeleteAction,
}: Omit<RoleDetailDrawerProps, 'role' | 'onCloseAction'> & { role: string }) {
  const [filter, setFilter] = useState('')

  const {
    control,
    handleSubmit,
    setError,
    formState: { isSubmitting, isDirty },
  } = useForm<RoleToolsValues>({
    resolver: zodResolver(roleToolsSchema),
    // The list may still be loading; the form re-seeds from the server tools via
    // `values` below so the checkboxes reflect the authoritative allowlist.
    values: { tools: coerceStringArray(roleTools) },
  })

  // The union of the catalogue and the role's current tools, so a tool granted to
  // the role that has since left the catalogue still renders (and can be removed).
  const allTools = useMemo(() => {
    const names = new Set<string>([
      ...coerceStringArray(availableTools),
      ...coerceStringArray(roleTools),
    ])
    return Array.from(names).sort((a, b) => a.localeCompare(b))
  }, [availableTools, roleTools])

  const submit = handleSubmit(async ({ tools }) => {
    const result = await onSaveToolsAction(role, tools)
    if (!result.current) return
    if (result.ok) {
      bigsuToast.success('Allowlist saved', { description: `Updated tools for ${role}.` })
      return
    }
    if (result.message) {
      setError('tools', { type: 'server', message: result.message })
      bigsuToast.danger('Could not save allowlist', { description: result.message })
    }
  })

  // ConfirmDialog keeps itself open when onConfirm rejects, so a failed delete
  // (e.g. ROLE_IN_USE) stays on screen for retry; a resolved one lets the
  // controller close the drawer once the row is gone.
  const confirmDelete = async () => {
    const result = await onDeleteAction(role)
    if (!result.current) return
    if (!result.ok) {
      if (result.message) bigsuToast.danger('Could not delete role', { description: result.message })
      throw new Error(result.message || 'delete failed')
    }
    bigsuToast.success('Role deleted', { description: role })
  }

  return (
    <DrawerContent>
      <DrawerHeader>
        <DrawerTitle>
          <span className="font-mono">{role}</span>
        </DrawerTitle>
        <DrawerDescription>Manage the tool allowlist for this role.</DrawerDescription>
      </DrawerHeader>

      <form id="role-tools-form" onSubmit={submit} className="flex min-h-0 flex-1 flex-col gap-4">
        <FormField label="Filter tools">
          <Input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Filter tools…"
            disabled={roleToolsLoading}
            autoComplete="off"
          />
        </FormField>

        <Controller
          control={control}
          name="tools"
          render={({ field, fieldState }) => (
            <ToolAllowlist
              allTools={allTools}
              filter={filter}
              value={field.value}
              onChange={field.onChange}
              loading={roleToolsLoading}
              loadError={roleToolsError}
              errorText={fieldState.error?.message}
            />
          )}
        />
      </form>

      <DrawerFooter>
        <ConfirmDialog
          tone="danger"
          title="Delete role?"
          description={`This permanently deletes the ${role} role and removes its tool assignments. This cannot be undone.`}
          confirmLabel="Delete role"
          onConfirm={confirmDelete}
          trigger={
            <Button variant="destructive" disabled={roleToolsLoading || isSubmitting}>
              Delete role
            </Button>
          }
        />
        <Button type="submit" form="role-tools-form" loading={isSubmitting} disabled={!isDirty}>
          Save allowlist
        </Button>
      </DrawerFooter>
    </DrawerContent>
  )
}

function ToolAllowlist({
  allTools,
  filter,
  value,
  onChange,
  loading,
  loadError,
  errorText,
}: {
  allTools: string[]
  filter: string
  value: string[]
  onChange: (tools: string[]) => void
  loading: boolean
  loadError: string | null
  errorText?: string
}) {
  const needle = filter.trim().toLowerCase()
  const visible = needle ? allTools.filter((tool) => tool.toLowerCase().includes(needle)) : allTools

  const toggle = (tool: string, checked: boolean) => {
    if (checked) onChange([...value, tool].sort((a, b) => a.localeCompare(b)))
    else onChange(value.filter((name) => name !== tool))
  }

  return (
    <FormField label="Tool allowlist" errorText={errorText ?? loadError ?? undefined}>
      <div className="max-h-[45vh] overflow-y-auto rounded-md border border-border-default bg-surface p-3">
        {loading ? (
          <p className="px-1 py-2 text-sm text-text-secondary">Loading role tools…</p>
        ) : visible.length === 0 ? (
          <p className="px-1 py-2 text-sm text-text-secondary">
            {allTools.length === 0 ? 'No tools available.' : 'No tools match the filter.'}
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {visible.map((tool) => (
              <li key={tool}>
                <Checkbox
                  checked={value.includes(tool)}
                  onCheckedChange={(checked) => toggle(tool, checked === true)}
                  label={<span className="font-mono text-sm text-text-primary">{tool}</span>}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </FormField>
  )
}
