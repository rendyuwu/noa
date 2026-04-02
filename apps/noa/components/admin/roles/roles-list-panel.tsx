import { RefreshCw, Shield, Sparkles } from "lucide-react";

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
            Manage role definitions and per-role tool allowlists from the shared shell using the normalized API client.
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex items-center gap-2 rounded-2xl border border-border bg-bg px-4 py-2.5 font-ui text-sm font-medium text-text transition hover:bg-surface-2"
        >
          <RefreshCw className="size-4" />
          Refresh
        </button>
      </div>

      <div className="mt-5 grid gap-3 rounded-2xl border border-border bg-bg/70 p-4">
        <label className="font-ui text-sm font-medium text-text" htmlFor="new-role-name">
          Create role
        </label>
        <div className="flex flex-col gap-3 sm:flex-row">
          <input
            id="new-role-name"
            value={newRoleName}
            onChange={(event) => onNewRoleNameChange(event.target.value)}
            placeholder="billing-ops"
            className="min-w-0 flex-1 rounded-2xl border border-border bg-surface px-4 py-3 text-sm text-text outline-none transition focus:border-accent"
          />
          <button
            type="button"
            onClick={onCreateRole}
            disabled={creating}
            className="inline-flex items-center justify-center gap-2 rounded-2xl bg-accent px-4 py-3 font-ui text-sm font-semibold text-accent-foreground disabled:opacity-70"
          >
            <Sparkles className="size-4" />
            {creating ? "Creating…" : "Create role"}
          </button>
        </div>
      </div>

      <div className="mt-5">
        <label className="font-ui text-sm font-medium text-text" htmlFor="roles-search">
          Search roles
        </label>
        <input
          id="roles-search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Filter roles"
          className="mt-2 w-full rounded-2xl border border-border bg-bg px-4 py-3 text-sm text-text outline-none transition focus:border-accent"
        />
      </div>

      {loadError ? (
        <div role="alert" className="mt-5 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 font-ui text-sm text-red-700">
          {loadError}
        </div>
      ) : null}

      <div className="mt-5 grid gap-3">
        {loading ? (
          <div className="rounded-2xl border border-dashed border-border px-4 py-8 font-ui text-sm text-muted">
            Loading roles…
          </div>
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
                  <div className="mt-1 font-ui text-sm text-muted">
                    {availableToolsCount} available tool{availableToolsCount === 1 ? "" : "s"}
                  </div>
                </div>
                <Shield className="size-4 shrink-0 text-accent" />
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
