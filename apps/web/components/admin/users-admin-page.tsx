"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Cross2Icon } from "@radix-ui/react-icons";
import * as Dialog from "@radix-ui/react-dialog";

import { Button } from "@/components/lib/button";
import { ConfirmAction } from "@/components/lib/confirm-dialog";
import { toUserMessage } from "@/components/lib/error-message";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";

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

type AdminRole = {
  name: string;
};

type AdminRolesResponse = {
  roles: AdminRole[] | string[];
};

function coerceStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function coerceRoleNames(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const strings = value.filter((v): v is string => typeof v === "string");
  if (strings.length) return strings;

  const names: string[] = [];
  for (const item of value) {
    if (item && typeof item === "object" && "name" in item && typeof (item as any).name === "string") {
      names.push((item as any).name);
    }
  }
  return names;
}

function sanitizeIdPart(value: string): string {
  return value.replace(/[^A-Za-z0-9_-]/g, "-");
}

function formatTimestamp(value: unknown): string {
  if (typeof value !== "string" || !value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "Z");
}

export function UsersAdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [availableRoles, setAvailableRoles] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadSeqRef = useRef(0);
  const panelSeqRef = useRef(0);
  const openerRef = useRef<HTMLElement | null>(null);
  const selectedUserIdRef = useRef<string | null>(null);

  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const selectedUser = useMemo(() => {
    if (!selectedUserId) return null;
    return users.find((u) => u.id === selectedUserId) ?? null;
  }, [selectedUserId, users]);

  useEffect(() => {
    selectedUserIdRef.current = selectedUserId;
  }, [selectedUserId]);

  const [roleFilter, setRoleFilter] = useState("");
  const [roleAssignments, setRoleAssignments] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [statusSaving, setStatusSaving] = useState(false);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [deleteSaving, setDeleteSaving] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    const seq = ++loadSeqRef.current;
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

      if (seq !== loadSeqRef.current) return;
      setUsers(Array.isArray(usersPayload.users) ? usersPayload.users : []);
      const roleNames = coerceRoleNames(rolesPayload.roles);
      setAvailableRoles(Array.from(new Set(roleNames)).sort((a, b) => a.localeCompare(b)));
    } catch (error) {
      if (seq !== loadSeqRef.current) return;
      setLoadError(toUserMessage(error, "Unable to load users"));
    } finally {
      if (seq !== loadSeqRef.current) return;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
    return () => {
      loadSeqRef.current += 1;
    };
  }, [loadData]);

  const allRoleNames = useMemo(() => {
    const merged = new Set<string>([...coerceStringArray(availableRoles), ...coerceStringArray(roleAssignments)]);
    return Array.from(merged).sort((a, b) => a.localeCompare(b));
  }, [availableRoles, roleAssignments]);

  const filteredRoleNames = useMemo(() => {
    const needle = roleFilter.trim().toLowerCase();
    if (!needle) return allRoleNames;
    return allRoleNames.filter((name) => name.toLowerCase().includes(needle));
  }, [allRoleNames, roleFilter]);

  const closePanel = () => {
    panelSeqRef.current += 1;
    selectedUserIdRef.current = null;
    setSelectedUserId(null);
    setRoleFilter("");
    setRoleAssignments([]);
    setSaveError(null);
    setSaving(false);
    setStatusError(null);
    setStatusSaving(false);
    setDeleteError(null);
    setDeleteSaving(false);
  };

  const openPanelForUser = (user: AdminUser, opener: HTMLElement | null) => {
    panelSeqRef.current += 1;
    openerRef.current = opener;
    selectedUserIdRef.current = user.id;
    setSelectedUserId(user.id);
    setRoleFilter("");
    setRoleAssignments(coerceStringArray(user.roles));
    setSaveError(null);
    setSaving(false);
    setStatusError(null);
    setStatusSaving(false);
    setDeleteError(null);
    setDeleteSaving(false);
  };

  const toggleRole = (roleName: string) => {
    setRoleAssignments((prev) => {
      if (prev.includes(roleName)) return prev.filter((r) => r !== roleName);
      return [...prev, roleName].sort((a, b) => a.localeCompare(b));
    });
  };

  const panelStillMatches = (seq: number, userId: string) => {
    return panelSeqRef.current === seq && selectedUserIdRef.current === userId;
  };

  const saveRoles = async () => {
    if (!selectedUser) return;

    const seq = panelSeqRef.current;
    const userId = selectedUser.id;
    const requestedRoles = roleAssignments.slice();

    setSaving(true);
    setSaveError(null);

    try {
      const response = await fetchWithAuth(`/admin/users/${userId}/roles`, {
        method: "PUT",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({ roles: requestedRoles }),
      });

      await jsonOrThrow(response);

      setUsers((prev) =>
        prev.map((u) => {
          if (u.id !== userId) return u;
          return { ...u, roles: requestedRoles };
        })
      );
      if (panelStillMatches(seq, userId)) {
        closePanel();
      }
    } catch (error) {
      if (panelStillMatches(seq, userId)) {
        setSaveError(toUserMessage(error, "Unable to save roles"));
      }
    } finally {
      if (panelStillMatches(seq, userId)) {
        setSaving(false);
      }
    }
  };

  const toggleUserStatus = async () => {
    if (!selectedUser) return;

    const seq = panelSeqRef.current;
    const userId = selectedUser.id;
    const nextIsActive = selectedUser.is_active === false;

    setStatusSaving(true);
    setStatusError(null);

    try {
      const response = await fetchWithAuth(`/admin/users/${userId}`, {
        method: "PATCH",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({ is_active: nextIsActive }),
      });

      await jsonOrThrow(response);

      setUsers((prev) =>
        prev.map((u) => {
          if (u.id !== userId) return u;
          return { ...u, is_active: nextIsActive };
        })
      );
    } catch (error) {
      if (panelStillMatches(seq, userId)) {
        setStatusError(toUserMessage(error, "Unable to update user status"));
      }
    } finally {
      if (panelStillMatches(seq, userId)) {
        setStatusSaving(false);
      }
    }
  };

  const deleteUser = async () => {
    if (!selectedUser) return;

    const seq = panelSeqRef.current;
    const userId = selectedUser.id;

    setDeleteSaving(true);
    setDeleteError(null);

    try {
      const response = await fetchWithAuth(`/admin/users/${userId}`, {
        method: "DELETE",
      });

      await jsonOrThrow(response);

      setUsers((prev) => prev.filter((user) => user.id !== userId));
      if (panelStillMatches(seq, userId)) {
        closePanel();
      }
    } catch (error) {
      if (panelStillMatches(seq, userId)) {
        setDeleteError(toUserMessage(error, "Unable to delete user"));
      }
    } finally {
      if (panelStillMatches(seq, userId)) {
        setDeleteSaving(false);
      }
    }
  };

  return (
    <Dialog.Root
      open={selectedUserId !== null}
      onOpenChange={(open) => {
        if (!open) closePanel();
      }}
    >
      <main className="min-h-dvh bg-bg p-6">
        <div className="flex items-end justify-between gap-3">
          <h1 className="text-2xl font-semibold">Users</h1>
          {loading ? <div className="muted font-ui">Loading...</div> : null}
        </div>

        {loadError ? (
          <div
            role="alert"
            aria-live="assertive"
            className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 font-ui text-sm text-red-800"
          >
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">{loadError}</div>
              <Button className="shrink-0" disabled={loading} onClick={() => void loadData()} size="sm">
                Retry
              </Button>
            </div>
          </div>
        ) : null}

        <div className="panel mt-6 overflow-hidden">
          <table className="w-full font-ui text-sm">
            <thead className="bg-surface-2 text-muted">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Email</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Created</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Last login</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Status</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Roles</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Tools</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {users.length === 0 ? (
                <tr>
                  <td className="px-4 py-4 text-sm text-muted" colSpan={7}>
                    {loading
                      ? "Loading users..."
                      : loadError
                          ? "Unable to load users."
                          : "No users found."}
                  </td>
                </tr>
              ) : (
                users.map((user) => {
                  const roles = coerceStringArray(user.roles);
                  const tools = coerceStringArray(user.tools);
                  const createdLabel = formatTimestamp(user.created_at);
                  const lastLoginLabel = user.last_login_at ? formatTimestamp(user.last_login_at) : "Never";
                  const isActive = user.is_active !== false;
                  const hasLoggedIn = Boolean(user.last_login_at);
                  const statusLabel = isActive
                    ? "Active"
                    : hasLoggedIn
                      ? "Disabled"
                      : "Pending approval";

                  return (
                    <tr
                      key={user.id}
                      tabIndex={0}
                      aria-haspopup="dialog"
                      aria-label={`Manage roles for ${user.email}`}
                      className="cursor-pointer transition-colors hover:bg-surface-2/60 focus-visible:bg-surface-2/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent/40"
                      onClick={(event) => openPanelForUser(user, event.currentTarget)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
                          event.preventDefault();
                          openPanelForUser(user, event.currentTarget);
                        }
                      }}
                    >
                      <td className="px-4 py-3">
                        <div className="font-medium text-text">{user.email}</div>
                        {user.display_name ? (
                          <div className="mt-0.5 text-xs text-muted">{user.display_name}</div>
                        ) : null}
                      </td>
                      <td className="px-4 py-3 text-muted">{createdLabel}</td>
                      <td className="px-4 py-3 text-muted">{lastLoginLabel}</td>
                      <td className="px-4 py-3 text-muted">{statusLabel}</td>
                      <td className="px-4 py-3 text-muted">{roles.length ? roles.join(", ") : "-"}</td>
                      <td className="px-4 py-3 text-muted">{tools.length}</td>
                      <td className="px-4 py-3 text-muted">Manage</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30 opacity-0 transition-opacity data-[state=open]:opacity-100 data-[state=closed]:opacity-0" />
          <Dialog.Content
            className={[
              "fixed inset-y-0 right-0 z-50 w-[30rem] max-w-[92vw]",
              "border-border border-l bg-bg shadow-md",
              "transition-transform duration-200 ease-out",
              "data-[state=open]:translate-x-0 data-[state=closed]:translate-x-full",
              "outline-none",
            ].join(" ")}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              openerRef.current?.focus();
            }}
          >
            <Dialog.Title className="sr-only">Manage user roles</Dialog.Title>
            <Dialog.Description className="sr-only">
              Edit the selected user's roles and review effective tools.
            </Dialog.Description>

            <div className="flex h-full flex-col">
              <div className="flex items-start justify-between gap-3 border-b border-border bg-surface px-4 py-4">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-text">
                    {selectedUser?.display_name ?? selectedUser?.email ?? "User"}
                  </div>
                  {selectedUser?.email ? (
                    <div className="mt-0.5 text-xs text-muted">{selectedUser.email}</div>
                  ) : null}
                </div>
                <Dialog.Close asChild>
                  <Button aria-label="Close" size="icon">
                    <Cross2Icon width={16} height={16} />
                  </Button>
                </Dialog.Close>
              </div>

              <div className="flex-1 min-h-0 overflow-y-auto p-4 font-ui">
                <div className="rounded-xl border border-border bg-surface px-3 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold uppercase tracking-wide text-muted">Status</div>
                      <div className="mt-1 text-sm text-text">
                        {selectedUser?.is_active === false ? "Inactive" : "Active"}
                      </div>
                    </div>
                    <Button
                      className="shrink-0"
                      disabled={!selectedUser || statusSaving}
                      variant={selectedUser?.is_active === false ? "primary" : "danger"}
                      onClick={() => void toggleUserStatus()}
                      size="sm"
                    >
                      {selectedUser?.is_active === false ? "Enable" : "Disable"}
                    </Button>
                  </div>
                </div>

                {statusError ? (
                  <p
                    role="alert"
                    aria-live="assertive"
                    className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
                  >
                    {statusError}
                  </p>
                ) : null}

                {deleteError ? (
                  <p
                    role="alert"
                    aria-live="assertive"
                    className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
                  >
                    {deleteError}
                  </p>
                ) : null}

                <div className="mt-4">
                  {saveError ? (
                    <p
                      role="alert"
                      aria-live="assertive"
                      className="mb-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
                    >
                      {saveError}
                    </p>
                  ) : null}

                  <label className="text-xs font-semibold uppercase tracking-wide text-muted" htmlFor="role-filter">
                    Role assignment
                  </label>
                  <input
                    id="role-filter"
                    className="input mt-2"
                    placeholder="Filter roles..."
                    value={roleFilter}
                    onChange={(e) => setRoleFilter(e.target.value)}
                  />

                  <div className="mt-3 overflow-hidden rounded-xl border border-border bg-surface">
                    <div className="max-h-[62vh] overflow-auto p-2">
                      {filteredRoleNames.length === 0 ? (
                        <div className="px-2 py-3 text-sm text-muted">No matching roles.</div>
                      ) : (
                        <ul className="space-y-1">
                          {filteredRoleNames.map((roleName) => {
                            const checked = roleAssignments.includes(roleName);
                            const roleIdPart = sanitizeIdPart(roleName);
                            const userIdPart = sanitizeIdPart(selectedUser?.id ?? "unknown");
                            const inputId = `role-${userIdPart}-${roleIdPart}`;

                            return (
                              <li key={roleName}>
                                <label
                                  className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-surface-2"
                                  htmlFor={inputId}
                                >
                                  <input
                                    id={inputId}
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() => toggleRole(roleName)}
                                  />
                                  <span className="text-sm text-text">{roleName}</span>
                                </label>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                  </div>

                  <div className="mt-4 rounded-xl border border-border bg-surface px-3 py-3">
                    <div className="text-xs font-semibold uppercase tracking-wide text-muted">Effective tools</div>
                    <div className="mt-2 overflow-hidden rounded-lg border border-border bg-bg/25">
                      <div className="max-h-48 overflow-auto p-2">
                        {coerceStringArray(selectedUser?.tools).length === 0 ? (
                          <div className="px-2 py-2 text-sm text-muted">No tools granted.</div>
                        ) : (
                          <ul className="space-y-1">
                            {coerceStringArray(selectedUser?.tools).map((toolName) => (
                              <li key={toolName} className="rounded-md px-2 py-1 font-mono text-[12px] text-text">
                                {toolName}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </div>
                  </div>

                  {coerceStringArray(selectedUser?.direct_tools).length ? (
                    <div className="mt-4 rounded-xl border border-border bg-surface px-3 py-3">
                      <div className="text-xs font-semibold uppercase tracking-wide text-muted">
                        Legacy direct grants
                      </div>
                      <p className="mt-1 text-sm text-muted">
                        These tools are granted directly to the user (legacy behavior) and are not managed by roles.
                      </p>
                      <div className="mt-2 overflow-hidden rounded-lg border border-border bg-bg/25">
                        <div className="max-h-48 overflow-auto p-2">
                          <ul className="space-y-1">
                            {coerceStringArray(selectedUser?.direct_tools).map((toolName) => (
                              <li key={toolName} className="rounded-md px-2 py-1 font-mono text-[12px] text-text">
                                {toolName}
                              </li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    </div>
                  ) : null}

                  <div className="mt-4 border-t border-border pt-4">
                    <Button
                      className="w-full"
                      disabled={!selectedUser || saving}
                      onClick={() => void saveRoles()}
                      variant="primary"
                    >
                      {saving ? "Saving..." : "Save"}
                    </Button>
                  </div>

                  <div className="danger-zone mt-6">
                    <div className="danger-zone-label text-xs font-semibold uppercase tracking-wide">
                      Danger zone
                    </div>
                    <p className="danger-zone-copy mt-1 text-sm">
                      Delete this user account and remove its access from NOA.
                    </p>
                    <ConfirmAction
                      title="Delete user?"
                      description={
                        selectedUser
                          ? `This permanently deletes ${selectedUser.email} and removes access from NOA.`
                          : "This permanently deletes this user and removes access from NOA."
                      }
                      confirmLabel="Delete user"
                      confirmBusyLabel="Deleting..."
                      confirmVariant="danger"
                      busy={deleteSaving}
                      error={deleteError}
                      onConfirm={() => void deleteUser()}
                      trigger={({ open, disabled }) => (
                        <Button
                          className="mt-3 w-full"
                          disabled={!selectedUser || disabled}
                          onClick={() => {
                            setDeleteError(null);
                            open();
                          }}
                          variant="danger"
                        >
                          Delete user
                        </Button>
                      )}
                    />
                  </div>
                </div>
              </div>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </main>
    </Dialog.Root>
  );
}
