import { ShieldCheck, Trash2, UserCog, UserRoundCheck, UserRoundX } from "lucide-react";

import { coerceStringArray, formatTimestampLocalized } from "@/components/admin/lib/admin-data";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";

import type { AdminUser } from "./types";

type UsersDetailPanelProps = {
  allRoleNames: string[];
  confirmDeleteOpen: boolean;
  deleting: boolean;
  onConfirmDeleteClose: () => void;
  onConfirmDeleteOpen: () => void;
  onDeleteUser: () => void;
  onSaveRoles: () => void;
  onToggleRole: (roleName: string) => void;
  onToggleUserStatus: () => void;
  roleAssignments: string[];
  savingRoles: boolean;
  selectedUser: AdminUser | null;
  updatingStatus: boolean;
};

export function UsersDetailPanel({
  allRoleNames,
  confirmDeleteOpen,
  deleting,
  onConfirmDeleteClose,
  onConfirmDeleteOpen,
  onDeleteUser,
  onSaveRoles,
  onToggleRole,
  onToggleUserStatus,
  roleAssignments,
  savingRoles,
  selectedUser,
  updatingStatus,
}: UsersDetailPanelProps) {
  const selectedUserState = selectedUser?.is_active === false ? "Inactive" : "Active";

  return (
    <section className="rounded-3xl border border-border bg-surface p-5 shadow-soft">
      {selectedUser ? (
        <>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">Selected user profile</p>
              <h2 className="mt-2 flex items-center gap-2 truncate text-2xl font-semibold tracking-[-0.02em] text-text">
                <span className="truncate">{selectedUser.display_name?.trim() || selectedUser.email}</span>
                <span className="shrink-0 font-ui text-base font-medium text-muted">· {selectedUserState}</span>
              </h2>
              <p className="mt-1 truncate font-ui text-sm text-muted">{selectedUser.email}</p>
            </div>
            <UserCog className="mt-1 size-5 shrink-0 text-accent" />
          </div>

          <div className="mt-5 rounded-2xl border border-border bg-bg/70 p-4">
            <div className="flex items-center gap-2">
              <ShieldCheck className="size-4 text-accent" />
              <h3 className="text-base font-semibold text-text">Account overview</h3>
            </div>
            <dl className="mt-3 grid gap-3 font-ui text-sm text-text">
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted">Created</dt>
                <dd>{formatTimestampLocalized(selectedUser.created_at)}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted">Last login</dt>
                <dd>{formatTimestampLocalized(selectedUser.last_login_at)}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-muted">Direct tools</dt>
                <dd>{coerceStringArray(selectedUser.direct_tools).length}</dd>
              </div>
            </dl>
          </div>

          <div className="mt-6 rounded-2xl border border-border bg-bg/70 p-4">
            <div className="flex items-center gap-2">
              <ShieldCheck className="size-4 text-accent" />
              <h3 className="text-base font-semibold text-text">Access control</h3>
            </div>
            <p className="mt-2 font-ui text-sm text-muted">
              Assign roles to control this user's access and permissions.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {allRoleNames.length > 0 ? (
                allRoleNames.map((roleName) => {
                  const selected = roleAssignments.includes(roleName);
                  return (
                    <Button
                      key={roleName}
                      type="button"
                      onClick={() => onToggleRole(roleName)}
                      variant={selected ? "default" : "outline"}
                      size="sm"
                      className="rounded-full font-ui text-sm"
                    >
                      {roleName}
                    </Button>
                  );
                })
              ) : (
                <div className="rounded-2xl border border-dashed border-border px-4 py-4 font-ui text-sm text-muted">
                  No roles are available yet.
                </div>
              )}
            </div>
            <div className="mt-6 flex flex-col gap-3">
              <Button
                type="button"
                onClick={onSaveRoles}
                disabled={savingRoles}
                className="w-full rounded-2xl font-ui text-sm font-semibold"
              >
                <ShieldCheck className="size-4" />
                {savingRoles ? "Saving roles…" : "Save roles"}
              </Button>
            </div>
          </div>

          <div className="mt-6 rounded-2xl border border-border bg-bg/70 p-4">
            <div className="flex items-center gap-2">
              <Trash2 className="size-4 text-destructive" />
              <h3 className="text-base font-semibold text-text">Danger zone</h3>
            </div>
            <p className="mt-2 font-ui text-sm text-muted">
              Disable or delete the account when access should be removed.
            </p>
            <div className="mt-4 flex flex-col gap-3">
              <Button
                type="button"
                onClick={onToggleUserStatus}
                disabled={updatingStatus}
                variant="outline"
                className="w-full rounded-2xl font-ui text-sm font-medium"
              >
                {selectedUser.is_active === false ? (
                  <UserRoundCheck className="size-4" />
                ) : (
                  <UserRoundX className="size-4" />
                )}
                {updatingStatus
                  ? "Updating status…"
                  : selectedUser.is_active === false
                    ? "Activate user"
                    : "Deactivate user"}
              </Button>
              <AlertDialog
                open={confirmDeleteOpen}
                onOpenChange={(open) => (open ? onConfirmDeleteOpen() : onConfirmDeleteClose())}
              >
                <AlertDialogTrigger asChild>
                  <Button
                    type="button"
                    disabled={deleting}
                    variant="destructive-outline"
                    className="w-full rounded-2xl font-ui text-sm font-medium"
                  >
                    <Trash2 className="size-4" />
                    {deleting ? "Deleting…" : "Delete user"}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Delete user</AlertDialogTitle>
                    <AlertDialogDescription>
                      Delete {selectedUser?.email}? This cannot be undone.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction onClick={onDeleteUser}>Delete</AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </div>
        </>
      ) : (
        <div className="flex min-h-[24rem] items-center justify-center rounded-2xl border border-dashed border-border px-4 py-8 text-center font-ui text-sm text-muted">
          Select a user to inspect role assignments and account status.
        </div>
      )}
    </section>
  );
}
