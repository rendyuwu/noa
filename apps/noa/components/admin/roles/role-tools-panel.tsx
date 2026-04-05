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
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

type RoleToolsPanelProps = {
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
              <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">Selected role details</p>
              <h2 className="mt-2 truncate text-2xl font-semibold tracking-[-0.02em] text-text">{selectedRoleName}</h2>
              <p className="mt-1 font-ui text-sm text-muted">
                Update the allowlist for backend tool access granted through this role.
              </p>
            </div>
            <Wand2 className="mt-1 size-5 shrink-0 text-accent" />
          </div>

          <div className="mt-5 rounded-2xl border border-border bg-bg/70 p-4">
            <div className="flex items-center gap-2">
              <Shield className="size-4 text-accent" />
              <h3 className="text-base font-semibold text-text">Access policy</h3>
            </div>
            <p className="mt-2 font-ui text-sm text-muted">
              Toggle the tools this role can access. Save the allowlist to apply changes.
            </p>
          </div>

          {roleToolsError ? (
            <Alert tone="destructive" className="mt-5">
              <AlertDescription>{roleToolsError}</AlertDescription>
            </Alert>
          ) : null}

          <div className="mt-5 flex flex-wrap gap-2">
            {roleToolsLoading ? (
              ["one", "two", "three", "four", "five", "six"].map((placeholder) => (
                <Skeleton key={placeholder} className="h-9 w-28 rounded-full" />
              ))
            ) : availableTools.length > 0 ? (
              availableTools.map((toolName) => {
                const selected = toolAllowlist.includes(toolName);
                return (
                  <Button
                    key={toolName}
                    type="button"
                    onClick={() => onToggleTool(toolName)}
                    variant={selected ? "default" : "outline"}
                    size="sm"
                    className="rounded-full"
                  >
                    {toolName}
                  </Button>
                );
              })
            ) : (
              <div className="rounded-2xl border border-dashed border-border px-4 py-6 font-ui text-sm text-muted">
                No tools are available yet.
              </div>
            )}
          </div>

          <div className="mt-6 flex flex-col gap-3">
            <Button
              type="button"
              onClick={onSaveRoleTools}
              disabled={saving || roleToolsLoading}
              className="rounded-2xl"
            >
              <Shield className="size-4" />
              {saving ? "Saving allowlist…" : "Save allowlist"}
            </Button>
            <Button
              type="button"
              onClick={onMigrateDirectGrants}
              disabled={migrating}
              variant="outline"
              className="rounded-2xl"
            >
              <Sparkles className="size-4" />
              {migrating ? "Migrating…" : "Migrate legacy direct grants"}
            </Button>
            <AlertDialog open={confirmDeleteOpen} onOpenChange={(open) => open ? onConfirmDeleteOpen() : onConfirmDeleteClose()}>
              <AlertDialogTrigger asChild>
                <Button
                  type="button"
                  disabled={deleting}
                  variant="destructive-outline"
                  className="rounded-2xl"
                >
                  <Trash2 className="size-4" />
                  {deleting ? "Deleting…" : "Delete role"}
                </Button>
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
