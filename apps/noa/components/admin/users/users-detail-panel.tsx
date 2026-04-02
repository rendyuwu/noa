import { ShieldCheck, Trash2, UserCog, UserRoundCheck, UserRoundX } from "lucide-react";

import { coerceStringArray, formatTimestamp } from "@/components/admin/lib/admin-data";
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
  actionError: string | null;
  actionMessage: string | null;
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

function roleBadgeClass(selected: boolean) {
  return selected
    ? "border-accent bg-accent text-accent-foreground"
    : "border-border bg-bg text-text hover:border-accent/40 hover:bg-surface-2";
}

export function UsersDetailPanel({
  actionError,
  actionMessage,
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
  return (
    <section className="rounded-3xl border border-border bg-surface p-5 shadow-soft">
      {selectedUser ? (
        <>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">Selected user</p>
              <h2 className="mt-2 truncate text-2xl font-semibold tracking-[-0.02em] text-text">
                {selectedUser.display_name?.trim() || selectedUser.email}
              </h2>
              <p className="mt-1 truncate font-ui text-sm text-muted">{selectedUser.email}</p>
            </div>
            <UserCog className="mt-1 size-5 shrink-0 text-accent" />
          </div>

          <dl className="mt-5 grid gap-3 rounded-2xl border border-border bg-bg/70 p-4 font-ui text-sm text-text">
            <div className="flex items-center justify-between gap-3">
              <dt className="text-muted">Created</dt>
              <dd>{formatTimestamp(selectedUser.created_at)}</dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt className="text-muted">Last login</dt>
              <dd>{formatTimestamp(selectedUser.last_login_at)}</dd>
            </div>
            <div className="flex items-center justify-between gap-3">
              <dt className="text-muted">Direct tools</dt>
              <dd>{coerceStringArray(selectedUser.direct_tools).length}</dd>
            </div>
          </dl>

          {actionError ? (
            <div role="alert" className="mt-5 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 font-ui text-sm text-red-700">
              {actionError}
            </div>
          ) : null}

          {actionMessage ? (
            <div className="mt-5 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 font-ui text-sm text-emerald-700">
              {actionMessage}
            </div>
          ) : null}

          <div className="mt-5">
            <div className="flex items-center gap-2">
              <ShieldCheck className="size-4 text-accent" />
              <h3 className="text-base font-semibold text-text">Role assignments</h3>
            </div>
            <p className="mt-2 font-ui text-sm text-muted">
              Assign backend-defined admin roles. Changes save through the same-origin proxy and shared HTTP layer.
            </p>

            <div className="mt-4 flex flex-wrap gap-2">
              {allRoleNames.length > 0 ? (
                allRoleNames.map((roleName) => {
                  const selected = roleAssignments.includes(roleName);
                  return (
                    <button
                      key={roleName}
                      type="button"
                      onClick={() => onToggleRole(roleName)}
                      className={[
                        "rounded-full border px-3 py-2 font-ui text-sm transition",
                        roleBadgeClass(selected),
                      ].join(" ")}
                    >
                      {roleName}
                    </button>
                  );
                })
              ) : (
                <div className="rounded-2xl border border-dashed border-border px-4 py-4 font-ui text-sm text-muted">
                  No roles are available yet.
                </div>
              )}
            </div>
          </div>

          <div className="mt-6 flex flex-col gap-3">
            <button
              type="button"
              onClick={onSaveRoles}
              disabled={savingRoles}
              className="inline-flex items-center justify-center gap-2 rounded-2xl bg-accent px-4 py-3 font-ui text-sm font-semibold text-accent-foreground disabled:opacity-70"
            >
              <ShieldCheck className="size-4" />
              {savingRoles ? "Saving roles…" : "Save roles"}
            </button>
            <button
              type="button"
              onClick={onToggleUserStatus}
              disabled={updatingStatus}
              className="inline-flex items-center justify-center gap-2 rounded-2xl border border-border bg-bg px-4 py-3 font-ui text-sm font-medium text-text transition hover:bg-surface-2 disabled:opacity-70"
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
            </button>
            <AlertDialog open={confirmDeleteOpen} onOpenChange={(open) => open ? onConfirmDeleteOpen() : onConfirmDeleteClose()}>
              <AlertDialogTrigger asChild>
                <button
                  type="button"
                  disabled={deleting}
                  className="inline-flex items-center justify-center gap-2 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 font-ui text-sm font-medium text-red-700 transition hover:bg-red-100 disabled:opacity-70"
                >
                  <Trash2 className="size-4" />
                  {deleting ? "Deleting…" : "Delete user"}
                </button>
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
        </>
      ) : (
        <div className="flex min-h-[24rem] items-center justify-center rounded-2xl border border-dashed border-border px-4 py-8 text-center font-ui text-sm text-muted">
          Select a user to inspect role assignments and account status.
        </div>
      )}
    </section>
  );
}
