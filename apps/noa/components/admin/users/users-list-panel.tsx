import { RefreshCw } from "lucide-react";

import { coerceStringArray, formatTimestamp } from "@/components/admin/lib/admin-data";

import type { AdminUser } from "./types";

type UsersListPanelProps = {
  filteredUsers: AdminUser[];
  loadError: string | null;
  loading: boolean;
  onRefresh: () => void;
  onSearchChange: (value: string) => void;
  onSelectUser: (userId: string) => void;
  search: string;
  selectedUserId: string | null;
};

export function UsersListPanel({
  filteredUsers,
  loadError,
  loading,
  onRefresh,
  onSearchChange,
  onSelectUser,
  search,
  selectedUserId,
}: UsersListPanelProps) {
  return (
    <section className="rounded-3xl border border-border bg-surface p-5 shadow-soft">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">Admin / Users</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-[-0.02em] text-text">User management</h2>
          <p className="mt-2 max-w-2xl font-ui text-sm leading-6 text-muted">
            Manage user activation, roles, and permissions.
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

      <div className="mt-5">
        <label className="font-ui text-sm font-medium text-text" htmlFor="users-search">
          Search users
        </label>
        <input
          id="users-search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Filter by name, email, role, or direct tool"
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
            Loading users…
          </div>
        ) : filteredUsers.length > 0 ? (
          filteredUsers.map((user) => {
            const isSelected = user.id === selectedUserId;
            const roles = coerceStringArray(user.roles);

            return (
              <button
                key={user.id}
                type="button"
                onClick={() => onSelectUser(user.id)}
                className={[
                  "rounded-2xl border px-4 py-4 text-left transition",
                  isSelected
                    ? "border-accent bg-accent/8 shadow-soft"
                    : "border-border bg-bg/70 hover:border-accent/35 hover:bg-surface-2",
                ].join(" ")}
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-base font-semibold text-text">
                        {user.display_name?.trim() || user.email}
                      </span>
                      <span
                        className={[
                          "rounded-full px-2.5 py-1 font-ui text-xs font-medium",
                          user.is_active === false ? "bg-red-100 text-red-700" : "bg-emerald-100 text-emerald-700",
                        ].join(" ")}
                      >
                        {user.is_active === false ? "Inactive" : "Active"}
                      </span>
                    </div>
                    <p className="mt-1 truncate font-ui text-sm text-muted">{user.email}</p>
                  </div>
                  <div className="font-ui text-xs text-muted">Last login: {formatTimestamp(user.last_login_at)}</div>
                </div>

                <div className="mt-3 flex flex-wrap gap-2">
                  {roles.length > 0 ? (
                    roles.map((role) => (
                      <span
                        key={role}
                        className="rounded-full border border-border bg-surface px-2.5 py-1 font-ui text-xs text-text"
                      >
                        {role}
                      </span>
                    ))
                  ) : (
                    <span className="font-ui text-xs text-muted">No roles assigned</span>
                  )}
                </div>
              </button>
            );
          })
        ) : (
          <div className="rounded-2xl border border-dashed border-border px-4 py-8 font-ui text-sm text-muted">
            No users match this filter.
          </div>
        )}
      </div>
    </section>
  );
}
