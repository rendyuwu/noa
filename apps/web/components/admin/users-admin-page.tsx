"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AdminDataRow } from "@/components/admin/admin-data-row";
import { AdminDetailModal } from "@/components/admin/admin-detail-modal";
import { AdminListLayout } from "@/components/admin/admin-list-layout";
import { AdminStatusBadge } from "@/components/admin/admin-status-badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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

type UpdateUserResponse = {
  user: AdminUser;
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

function formatRelativeTime(value: unknown): string {
  if (typeof value !== "string" || !value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Never";

  const now = Date.now();
  const diffMs = now - date.getTime();
  if (diffMs < 0) return "Just now";

  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return "Just now";

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;

  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;

  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function formatDate(value: unknown): string {
  if (typeof value !== "string" || !value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";

  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function getUserStatus(user: AdminUser): { label: string; tone: "success" | "danger" | "warning" } {
  const isActive = user.is_active !== false;
  if (isActive) return { label: "Active", tone: "success" };
  const hasLoggedIn = Boolean(user.last_login_at);
  if (hasLoggedIn) return { label: "Disabled", tone: "danger" };
  return { label: "Pending", tone: "warning" };
}

function StatusBadge({ user }: { user: AdminUser }) {
  const { label, tone } = getUserStatus(user);
  return <AdminStatusBadge tone={tone}>{label}</AdminStatusBadge>;
}

export function UsersAdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [availableRoles, setAvailableRoles] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadSeqRef = useRef(0);
  const panelSeqRef = useRef(0);
  const selectedUserIdRef = useRef<string | null>(null);

  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const selectedUser = useMemo(() => {
    if (!selectedUserId) return null;
    return users.find((u) => u.id === selectedUserId) ?? null;
  }, [selectedUserId, users]);

  useEffect(() => {
    selectedUserIdRef.current = selectedUserId;
  }, [selectedUserId]);

  const [searchFilter, setSearchFilter] = useState("");
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
      if (seq === loadSeqRef.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadData();
    return () => {
      loadSeqRef.current += 1;
    };
  }, [loadData]);

  const filteredUsers = useMemo(() => {
    const needle = searchFilter.trim().toLowerCase();
    if (!needle) return users;
    return users.filter(
      (u) =>
        u.email.toLowerCase().includes(needle) ||
        (u.display_name && u.display_name.toLowerCase().includes(needle))
    );
  }, [users, searchFilter]);

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

  const openPanelForUser = (user: AdminUser) => {
    panelSeqRef.current += 1;
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

      const payload = await jsonOrThrow<UpdateUserResponse>(response);
      const updatedUser = payload.user;

      setUsers((prev) => prev.map((u) => (u.id === userId ? updatedUser : u)));
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
    <>
      <AdminListLayout
        title="Users"
        description="Manage user accounts, roles, and access."
        loading={loading}
        error={loadError}
        onRetry={() => void loadData()}
        empty={!loading && !loadError && users.length === 0}
        emptyTitle="No users yet"
        emptyDescription="Users will appear here after they sign in or are provisioned."
        filter={
          <Input
            aria-label="Search users"
            placeholder="Search by email or name..."
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
          />
        }
        actions={
          <Button disabled={loading} onClick={() => void loadData()} size="sm" variant="outline">
            Refresh
          </Button>
        }
      >
        <div className="panel overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full border-collapse text-left">
              <thead className="bg-accent/40 text-accent-foreground">
                <tr>
                  <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                    User
                  </th>
                  <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                    Status
                  </th>
                  <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                    Role
                  </th>
                  <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                    Created
                  </th>
                  <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                    Last login
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredUsers.map((user) => {
                  const roles = coerceStringArray(user.roles);
                  const subtitle =
                    user.display_name && user.display_name !== user.email
                      ? user.display_name
                      : undefined;

                  return (
                    <AdminDataRow
                      key={user.id}
                      selected={selectedUserId === user.id}
                      onClick={() => openPanelForUser(user)}
                      primaryAction={
                        <div>
                          <span className="block text-sm font-medium text-foreground">
                            {user.email}
                          </span>
                          {subtitle ? <span className="mt-1 block text-sm text-muted-foreground">{subtitle}</span> : null}
                        </div>
                      }
                      statusCell={<StatusBadge user={user} />}
                      roleCell={
                        roles.length > 0 ? (
                          <div className="flex flex-wrap gap-1.5">
                            {roles.map((role) => (
                              <AdminStatusBadge key={role} tone="outline">
                                {role}
                              </AdminStatusBadge>
                            ))}
                          </div>
                        ) : (
                          <span className="text-sm text-muted-foreground">No roles</span>
                        )
                      }
                      createdCell={formatDate(user.created_at)}
                      lastLoginCell={formatRelativeTime(user.last_login_at)}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </AdminListLayout>

      <AdminDetailModal
        open={selectedUserId !== null}
        onOpenChange={(open) => { if (!open) closePanel(); }}
        title={selectedUser?.display_name ?? selectedUser?.email ?? "User"}
        subtitle={selectedUser?.email}
        size="lg"
        headerActions={
          <Button
            className="shrink-0"
            disabled={!selectedUser || statusSaving}
            variant={selectedUser?.is_active === false ? "default" : "destructive"}
            onClick={() => void toggleUserStatus()}
            size="sm"
          >
            {selectedUser?.is_active === false ? "Enable" : "Disable"}
          </Button>
        }
        footer={
          <Button
            className="w-full"
            disabled={!selectedUser || saving}
            onClick={() => void saveRoles()}
            variant="default"
          >
            {saving ? "Saving..." : "Save"}
          </Button>
        }
      >
        <div className="editorial-subpanel">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Status</div>
          <div className="mt-2">
            {selectedUser ? <StatusBadge user={selectedUser} /> : <AdminStatusBadge>Unknown</AdminStatusBadge>}
          </div>
        </div>

        {statusError ? (
          <p
            role="alert"
            aria-live="assertive"
            className="mt-3 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {statusError}
          </p>
        ) : null}

        {deleteError ? (
          <p
            role="alert"
            aria-live="assertive"
            className="mt-3 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {deleteError}
          </p>
        ) : null}

        {/* Role assignment section */}
        <div className="mt-4">
          {saveError ? (
            <p
              role="alert"
              aria-live="assertive"
              className="mb-3 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              {saveError}
            </p>
          ) : null}

          <div className="editorial-subpanel">
            <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground" htmlFor="role-filter">
              Role assignment
            </label>
            <input
              id="role-filter"
              className="input mt-2 w-full"
              placeholder="Filter roles..."
              value={roleFilter}
              onChange={(e) => setRoleFilter(e.target.value)}
            />

            <div className="mt-3 overflow-hidden rounded-xl border border-border/70 bg-background/30">
              <div className="max-h-[50vh] overflow-y-auto p-2">
                {filteredRoleNames.length === 0 ? (
                  <div className="px-2 py-3 text-sm text-muted-foreground">No matching roles.</div>
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
                            className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-accent"
                            htmlFor={inputId}
                          >
                            <input
                              id={inputId}
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggleRole(roleName)}
                            />
                            <span className="text-sm text-foreground">{roleName}</span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </div>
          </div>

          {/* Effective tools */}
          <div className="editorial-subpanel mt-4">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Effective tools</div>
            <div className="mt-2 overflow-hidden rounded-xl border border-border/70 bg-background/30">
              <div className="max-h-48 overflow-y-auto p-2">
                {coerceStringArray(selectedUser?.tools).length === 0 ? (
                  <div className="px-2 py-2 text-sm text-muted-foreground">No tools granted.</div>
                ) : (
                  <ul className="space-y-1">
                    {coerceStringArray(selectedUser?.tools).map((toolName) => (
                      <li key={toolName} className="rounded-md px-2 py-1 font-mono text-[12px] text-foreground">
                        {toolName}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>

          {/* Legacy direct grants */}
          {coerceStringArray(selectedUser?.direct_tools).length ? (
            <div className="editorial-subpanel mt-4">
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Legacy direct grants
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                These tools were granted directly to the user (legacy behavior). Direct grants are no longer
                editable here.
              </p>

              <div className="mt-2 overflow-hidden rounded-xl border border-border/70 bg-background/30">
                <div className="max-h-48 overflow-y-auto p-2">
                  <ul className="space-y-1">
                    {coerceStringArray(selectedUser?.direct_tools).map((toolName) => (
                      <li key={toolName} className="rounded-md px-2 py-1 font-mono text-[12px] text-foreground">
                        {toolName}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          ) : null}

          {/* Danger zone */}
          <div className="danger-zone mt-6">
            <div className="danger-zone-label">Danger zone</div>
            <p className="danger-zone-copy">
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
                  variant="destructive"
                >
                  Delete user
                </Button>
              )}
            />
          </div>
        </div>
      </AdminDetailModal>
    </>
  );
}
