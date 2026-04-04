import { RefreshCw, Shield, Sparkles } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { TableSkeleton } from "@/components/ui/table-skeleton";

type RolesListPanelProps = {
  availableToolsCount: number;
  creating: boolean;
  filteredRoles: string[];
  loadError: string | null;
  loading: boolean;
  newRoleName: string;
  onCreateRole: () => void;
  onNewRoleNameChange: (value: string) => void;
  onRefresh: () => void;
  onSearchChange: (value: string) => void;
  onSelectRole: (role: string) => void;
  search: string;
  selectedRoleName: string | null;
};

export function RolesListPanel({
  availableToolsCount,
  creating,
  filteredRoles,
  loadError,
  loading,
  newRoleName,
  onCreateRole,
  onNewRoleNameChange,
  onRefresh,
  onSearchChange,
  onSelectRole,
  search,
  selectedRoleName,
}: RolesListPanelProps) {
  return (
    <section className="rounded-3xl border border-border bg-surface p-5 shadow-soft">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">Admin / Roles</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-[-0.02em] text-text">Roles and tool access</h2>
          <p className="mt-2 max-w-2xl font-ui text-sm leading-6 text-muted">
            Manage role definitions and per-role tool access.
          </p>
        </div>
        <Button
          type="button"
          onClick={onRefresh}
          variant="outline"
          className="rounded-2xl"
        >
          <RefreshCw className="size-4" />
          Refresh
        </Button>
      </div>

      <div className="mt-5 grid gap-3 rounded-2xl border border-border bg-bg/70 p-4">
        <label className="font-ui text-sm font-medium text-text" htmlFor="new-role-name">
          Create role
        </label>
        <div className="flex flex-col gap-3 sm:flex-row">
          <Input
            id="new-role-name"
            value={newRoleName}
            onChange={(event) => onNewRoleNameChange(event.target.value)}
            placeholder="billing-ops"
            className="min-w-0 flex-1 rounded-2xl border-border bg-surface px-4 py-3 text-sm"
          />
          <Button
            type="button"
            onClick={onCreateRole}
            disabled={creating}
            className="rounded-2xl"
          >
            <Sparkles className="size-4" />
            {creating ? "Creating…" : "Create role"}
          </Button>
        </div>
      </div>

      <div className="mt-5">
        <label className="font-ui text-sm font-medium text-text" htmlFor="roles-search">
          Search roles
        </label>
        <Input
          id="roles-search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Filter roles"
          className="mt-2 w-full rounded-2xl border-border bg-bg px-4 py-3 text-sm"
        />
      </div>

      {loadError ? (
        <Alert tone="destructive" className="mt-5">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      <div className="mt-5 grid gap-3">
        {loading ? (
          <TableSkeleton columns={2} rows={5} />
        ) : filteredRoles.length > 0 ? (
          filteredRoles.map((role) => (
            <button
              key={role}
              type="button"
              onClick={() => onSelectRole(role)}
              className={[
                "rounded-2xl border px-4 py-4 text-left transition",
                role === selectedRoleName
                  ? "border-accent bg-accent/8 shadow-soft"
                  : "border-border bg-bg/70 hover:border-accent/35 hover:bg-surface-2",
              ].join(" ")}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate text-base font-semibold text-text">{role}</div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="outline" className="rounded-full px-3 py-1 font-ui text-xs font-medium text-muted">
                    {availableToolsCount} available tool{availableToolsCount === 1 ? "" : "s"}
                  </Badge>
                  <Shield className="size-4 shrink-0 text-accent" />
                </div>
              </div>
            </button>
          ))
        ) : (
          <div className="rounded-2xl border border-dashed border-border px-4 py-8 font-ui text-sm text-muted">
            No roles match this filter.
          </div>
        )}
      </div>
    </section>
  );
}
