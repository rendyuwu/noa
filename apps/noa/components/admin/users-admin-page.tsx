"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, ShieldCheck, Trash2, UserCog, UserRoundCheck, UserRoundX } from "lucide-react";

import { fetchWithAuth, jsonOrThrow } from "@/components/lib/http/fetch-client";
import { toErrorMessage } from "@/components/lib/http/error-message";

type AdminUser = {
  id: string;
  email: string;
  display_name?: string | null;
  created_at?: string;
  last_login_at?: string | null;
  is_active?: boolean;
  roles?: string[];
  tools?: string[];
  direct_tools?: string[];
};

type AdminUsersResponse = {
  users: AdminUser[];
};

type UpdateUserResponse = {
  user: AdminUser;
};

type AdminRolesResponse = {
  roles: Array<{ name: string }> | string[];
};

function coerceStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === "string") : [];
}

function coerceRoleNames(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const direct = value.filter((entry): entry is string => typeof entry === "string");
  if (direct.length > 0) {
    return direct;
  }

  return value.flatMap((entry) => {
    if (entry && typeof entry === "object" && "name" in entry && typeof entry.name === "string") {
      return [entry.name];
    }

    return [];
  });
}

function formatTimestamp(value: unknown) {
  if (typeof value !== "string" || !value) {
    return "—";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function roleBadgeClass(selected: boolean) {
  return selected
    ? "border-accent bg-accent text-accent-foreground"
    : "border-border bg-bg text-text hover:border-accent/40 hover:bg-surface-2";
}

export function UsersAdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [availableRoles, setAvailableRoles] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [roleAssignments, setRoleAssignments] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [savingRoles, setSavingRoles] = useState(false);
  const [updatingStatus, setUpdatingStatus] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const selectedUser = useMemo(
    () => users.find((user) => user.id === selectedUserId) ?? null,
    [selectedUserId, users],
  );

  const filteredUsers = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) {
      return users;
    }

    return users.filter((user) => {
      const haystack = [
        user.display_name ?? "",
        user.email,
        ...coerceStringArray(user.roles),
        ...coerceStringArray(user.direct_tools),
      ]
        .join(" ")
        .toLowerCase();

      return haystack.includes(needle);
    });
  }, [search, users]);

  const allRoleNames = useMemo(() => {
    return Array.from(new Set([...availableRoles, ...coerceStringArray(selectedUser?.roles)])).sort((a, b) =>
      a.localeCompare(b),
    );
  }, [availableRoles, selectedUser?.roles]);

  const loadData = useCallback(async () => {
    setLoading(true);
    setLoadError(null);

    try {
      const [usersResponse, rolesResponse] = await Promise.all([
        fetchWithAuth("/admin/users"),
        fetchWithAuth("/admin/roles"),
      ]);

      const [usersPayload, rolesPayload] = await Promise.all([
        jsonOrThrow<AdminUsersResponse>(usersResponse),
        jsonOrThrow<AdminRolesResponse>(rolesResponse),
      ]);

      const nextUsers = Array.isArray(usersPayload.users) ? usersPayload.users : [];
      const nextRoles = Array.from(new Set(coerceRoleNames(rolesPayload.roles))).sort((a, b) =>
        a.localeCompare(b),
      );

      setUsers(nextUsers);
      setAvailableRoles(nextRoles);
      setSelectedUserId((current) => {
        if (current && nextUsers.some((user) => user.id === current)) {
          return current;
        }

        return nextUsers[0]?.id ?? null;
      });
    } catch (error) {
      setLoadError(toErrorMessage(error, "Unable to load users"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    setRoleAssignments(coerceStringArray(selectedUser?.roles).slice().sort((a, b) => a.localeCompare(b)));
    setActionError(null);
    setActionMessage(null);
  }, [selectedUser]);

  function toggleRole(roleName: string) {
    setRoleAssignments((current) => {
      if (current.includes(roleName)) {
        return current.filter((entry) => entry !== roleName);
      }

      return [...current, roleName].sort((a, b) => a.localeCompare(b));
    });
  }

  async function saveRoles() {
    if (!selectedUser) {
      return;
    }

    setSavingRoles(true);
    setActionError(null);
    setActionMessage(null);

    try {
      const response = await fetchWithAuth(`/admin/users/${selectedUser.id}/roles`, {
        method: "PUT",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({ roles: roleAssignments }),
      });
      const payload = await jsonOrThrow<UpdateUserResponse>(response);

      setUsers((current) => current.map((user) => (user.id === selectedUser.id ? payload.user : user)));
      setActionMessage("Roles updated.");
    } catch (error) {
      setActionError(toErrorMessage(error, "Unable to update user roles"));
    } finally {
      setSavingRoles(false);
    }
  }

  async function toggleUserStatus() {
    if (!selectedUser) {
      return;
    }

    setUpdatingStatus(true);
    setActionError(null);
    setActionMessage(null);

    const nextIsActive = selectedUser.is_active === false;

    try {
      const response = await fetchWithAuth(`/admin/users/${selectedUser.id}`, {
        method: "PATCH",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({ is_active: nextIsActive }),
      });
      const payload = await jsonOrThrow<UpdateUserResponse>(response);
      setUsers((current) => current.map((user) => (user.id === selectedUser.id ? payload.user : user)));
      setActionMessage(nextIsActive ? "User activated." : "User deactivated.");
    } catch (error) {
      setActionError(toErrorMessage(error, "Unable to update user status"));
    } finally {
      setUpdatingStatus(false);
    }
  }

  async function deleteUser() {
    if (!selectedUser) {
      return;
    }

    const confirmed = window.confirm(`Delete ${selectedUser.email}? This cannot be undone.`);
    if (!confirmed) {
      return;
    }

    setDeleting(true);
    setActionError(null);
    setActionMessage(null);

    try {
      const response = await fetchWithAuth(`/admin/users/${selectedUser.id}`, {
        method: "DELETE",
      });
      await jsonOrThrow<{ ok: boolean }>(response);

      setUsers((current) => current.filter((user) => user.id !== selectedUser.id));
      setSelectedUserId((current) => (current === selectedUser.id ? null : current));
      setActionMessage("User deleted.");
    } catch (error) {
      setActionError(toErrorMessage(error, "Unable to delete user"));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
      <section className="rounded-3xl border border-border bg-surface p-5 shadow-soft">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">Admin / Users</p>
            <h2 className="mt-2 text-2xl font-semibold tracking-[-0.02em] text-text">User management</h2>
            <p className="mt-2 max-w-2xl font-ui text-sm leading-6 text-muted">
              Manage activation and role assignments from the shared admin shell without the old per-route wrapper duplication.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadData()}
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
            onChange={(event) => setSearch(event.target.value)}
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
                  onClick={() => setSelectedUserId(user.id)}
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
                            user.is_active === false
                              ? "bg-red-100 text-red-700"
                              : "bg-emerald-100 text-emerald-700",
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
                        onClick={() => toggleRole(roleName)}
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
                onClick={() => void saveRoles()}
                disabled={savingRoles}
                className="inline-flex items-center justify-center gap-2 rounded-2xl bg-accent px-4 py-3 font-ui text-sm font-semibold text-accent-foreground disabled:opacity-70"
              >
                <ShieldCheck className="size-4" />
                {savingRoles ? "Saving roles…" : "Save roles"}
              </button>
              <button
                type="button"
                onClick={() => void toggleUserStatus()}
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
              <button
                type="button"
                onClick={() => void deleteUser()}
                disabled={deleting}
                className="inline-flex items-center justify-center gap-2 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 font-ui text-sm font-medium text-red-700 transition hover:bg-red-100 disabled:opacity-70"
              >
                <Trash2 className="size-4" />
                {deleting ? "Deleting…" : "Delete user"}
              </button>
            </div>
          </>
        ) : (
          <div className="flex min-h-[24rem] items-center justify-center rounded-2xl border border-dashed border-border px-4 py-8 text-center font-ui text-sm text-muted">
            Select a user to inspect role assignments and account status.
          </div>
        )}
      </section>
    </div>
  );
}
