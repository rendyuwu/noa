import { Shield, Sparkles, Trash2, Wand2 } from "lucide-react";

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

function toolBadgeClass(selected: boolean) {
  return selected
    ? "border-accent bg-accent text-accent-foreground"
    : "border-border bg-bg text-text hover:border-accent/40 hover:bg-surface-2";
}

type RoleToolsPanelProps = {
  actionError: string | null;
  actionMessage: string | null;
  availableTools: string[];
  confirmDeleteOpen: boolean;
  deleting: boolean;
  migrating: boolean;
  onConfirmDeleteClose: () => void;
  onConfirmDeleteOpen: () => void;
  onDeleteRole: () => void;
  onMigrateDirectGrants: () => void;
  onSaveRoleTools: () => void;
  onToggleTool: (toolName: string) => void;
  roleToolsError: string | null;
  roleToolsLoading: boolean;
  saving: boolean;
  selectedRoleName: string | null;
  toolAllowlist: string[];
};

export function RoleToolsPanel({
  actionError,
  actionMessage,
  availableTools,
  confirmDeleteOpen,
  deleting,
  migrating,
  onConfirmDeleteClose,
  onConfirmDeleteOpen,
  onDeleteRole,
  onMigrateDirectGrants,
  onSaveRoleTools,
  onToggleTool,
  roleToolsError,
  roleToolsLoading,
  saving,
  selectedRoleName,
  toolAllowlist,
}: RoleToolsPanelProps) {
  return (
    <section className="rounded-3xl border border-border bg-surface p-5 shadow-soft">
      {selectedRoleName ? (
        <>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">Selected role</p>
              <h2 className="mt-2 truncate text-2xl font-semibold tracking-[-0.02em] text-text">{selectedRoleName}</h2>
              <p className="mt-1 font-ui text-sm text-muted">
                Update the allowlist for backend tool access granted through this role.
              </p>
            </div>
            <Wand2 className="mt-1 size-5 shrink-0 text-accent" />
          </div>

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
              <Shield className="size-4 text-accent" />
              <h3 className="text-base font-semibold text-text">Tool allowlist</h3>
            </div>
            <p className="mt-2 font-ui text-sm text-muted">
              Toggle the tools this role can access. Changes are saved immediately.
            </p>
          </div>

          {roleToolsError ? (
            <div role="alert" className="mt-5 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 font-ui text-sm text-red-700">
              {roleToolsError}
            </div>
          ) : null}

          <div className="mt-5 flex flex-wrap gap-2">
            {roleToolsLoading ? (
              <div className="rounded-2xl border border-dashed border-border px-4 py-6 font-ui text-sm text-muted">
                Loading role tools…
              </div>
            ) : availableTools.length > 0 ? (
              availableTools.map((toolName) => {
                const selected = toolAllowlist.includes(toolName);
                return (
                  <button
                    key={toolName}
                    type="button"
                    onClick={() => onToggleTool(toolName)}
                    className={["rounded-full border px-3 py-2 font-ui text-sm transition", toolBadgeClass(selected)].join(" ")}
                  >
                    {toolName}
                  </button>
                );
              })
            ) : (
              <div className="rounded-2xl border border-dashed border-border px-4 py-6 font-ui text-sm text-muted">
                No tools are available yet.
              </div>
            )}
          </div>

          <div className="mt-6 flex flex-col gap-3">
            <button
              type="button"
              onClick={onSaveRoleTools}
              disabled={saving || roleToolsLoading}
              className="inline-flex items-center justify-center gap-2 rounded-2xl bg-accent px-4 py-3 font-ui text-sm font-semibold text-accent-foreground disabled:opacity-70"
            >
              <Shield className="size-4" />
              {saving ? "Saving allowlist…" : "Save allowlist"}
            </button>
            <button
              type="button"
              onClick={onMigrateDirectGrants}
              disabled={migrating}
              className="inline-flex items-center justify-center gap-2 rounded-2xl border border-border bg-bg px-4 py-3 font-ui text-sm font-medium text-text transition hover:bg-surface-2 disabled:opacity-70"
            >
              <Sparkles className="size-4" />
              {migrating ? "Migrating…" : "Migrate legacy direct grants"}
            </button>
            <AlertDialog open={confirmDeleteOpen} onOpenChange={(open) => open ? onConfirmDeleteOpen() : onConfirmDeleteClose()}>
              <AlertDialogTrigger asChild>
                <button
                  type="button"
                  disabled={deleting}
                  className="inline-flex items-center justify-center gap-2 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 font-ui text-sm font-medium text-red-700 transition hover:bg-red-100 disabled:opacity-70"
                >
                  <Trash2 className="size-4" />
                  {deleting ? "Deleting…" : "Delete role"}
                </button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Delete role</AlertDialogTitle>
                  <AlertDialogDescription>
                    Delete role {selectedRoleName}? This cannot be undone.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={onDeleteRole}>Delete</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </>
      ) : (
        <div className="flex min-h-[24rem] items-center justify-center rounded-2xl border border-dashed border-border px-4 py-8 text-center font-ui text-sm text-muted">
          Select a role to manage its tool allowlist and migration helpers.
        </div>
      )}
    </section>
  );
}
