"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { coerceRoleNames, coerceStringArray } from "@/components/admin/lib/admin-data";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/http/fetch-client";
import { toErrorMessage } from "@/components/lib/http/error-message";

import { UsersDetailPanel } from "./users/users-detail-panel";
import { UsersListPanel } from "./users/users-list-panel";
import type { AdminRolesResponse, AdminUser, AdminUsersResponse, UpdateUserResponse } from "./users/types";

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
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);

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
  }, [selectedUser]);

  useEffect(() => {
    if (filteredUsers.length === 0) {
      if (selectedUserId !== null) {
        setSelectedUserId(null);
      }
      return;
    }

    if (selectedUserId && filteredUsers.some((user) => user.id === selectedUserId)) {
      return;
    }

    setSelectedUserId(filteredUsers[0]?.id ?? null);
  }, [filteredUsers, selectedUserId]);

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
      toast.success("Roles updated.");
    } catch (error) {
      toast.error(toErrorMessage(error, "Unable to update user roles"));
    } finally {
      setSavingRoles(false);
    }
  }

  async function toggleUserStatus() {
    if (!selectedUser) {
      return;
    }

    setUpdatingStatus(true);

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
      toast.success(nextIsActive ? "User activated." : "User deactivated.");
    } catch (error) {
      toast.error(toErrorMessage(error, "Unable to update user status"));
    } finally {
      setUpdatingStatus(false);
    }
  }

  async function deleteUser() {
    if (!selectedUser) {
      return;
    }

    setConfirmDeleteOpen(false);
    setDeleting(true);

    try {
      const response = await fetchWithAuth(`/admin/users/${selectedUser.id}`, {
        method: "DELETE",
      });
      await jsonOrThrow<{ ok: boolean }>(response);

      setUsers((current) => current.filter((user) => user.id !== selectedUser.id));
      setSelectedUserId((current) => (current === selectedUser.id ? null : current));
      toast.success("User deleted.");
    } catch (error) {
      toast.error(toErrorMessage(error, "Unable to delete user"));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(320px,0.85fr)]">
      <UsersListPanel
        filteredUsers={filteredUsers}
        loadError={loadError}
        loading={loading}
        onRefresh={() => void loadData()}
        onSearchChange={setSearch}
        onSelectUser={setSelectedUserId}
        search={search}
        selectedUserId={selectedUserId}
      />
      <UsersDetailPanel
        allRoleNames={allRoleNames}
        confirmDeleteOpen={confirmDeleteOpen}
        deleting={deleting}
        onConfirmDeleteClose={() => setConfirmDeleteOpen(false)}
        onConfirmDeleteOpen={() => setConfirmDeleteOpen(true)}
        onDeleteUser={() => void deleteUser()}
        onSaveRoles={() => void saveRoles()}
        onToggleRole={toggleRole}
        onToggleUserStatus={() => void toggleUserStatus()}
        roleAssignments={roleAssignments}
        savingRoles={savingRoles}
        selectedUser={selectedUser}
        updatingStatus={updatingStatus}
      />
    </div>
  );
}
