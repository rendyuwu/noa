"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { coerceRoleNames, coerceStringArray } from "@/components/admin/lib/admin-data";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/http/fetch-client";
import { toErrorMessage } from "@/components/lib/http/error-message";

import { formatMigrationSummary } from "./roles/format-migration-summary";
import { RoleToolsPanel } from "./roles/role-tools-panel";
import { RolesListPanel } from "./roles/roles-list-panel";
import type {
  AdminRoleToolsResponse,
  AdminRolesResponse,
  AdminToolsResponse,
  DirectGrantsMigrationResponse,
} from "./roles/types";

export function RolesAdminPage() {
  const [roles, setRoles] = useState<string[]>([]);
  const [availableTools, setAvailableTools] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedRoleName, setSelectedRoleName] = useState<string | null>(null);
  const [toolAllowlist, setToolAllowlist] = useState<string[]>([]);
  const [roleToolsLoading, setRoleToolsLoading] = useState(false);
  const [roleToolsError, setRoleToolsError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [migrating, setMigrating] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [newRoleName, setNewRoleName] = useState("");
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState("");

  const filteredRoles = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) {
      return roles;
    }

    return roles.filter((role) => role.toLowerCase().includes(needle));
  }, [roles, search]);

  const loadData = useCallback(async () => {
    setLoading(true);
    setLoadError(null);

    try {
      const [rolesResponse, toolsResponse] = await Promise.all([
        fetchWithAuth("/admin/roles"),
        fetchWithAuth("/admin/tools"),
      ]);
      const [rolesPayload, toolsPayload] = await Promise.all([
        jsonOrThrow<AdminRolesResponse>(rolesResponse),
        jsonOrThrow<AdminToolsResponse>(toolsResponse),
      ]);

      const nextRoles = Array.from(new Set(coerceRoleNames(rolesPayload.roles))).sort((a, b) =>
        a.localeCompare(b),
      );
      const nextTools = Array.from(new Set(coerceStringArray(toolsPayload.tools))).sort((a, b) =>
        a.localeCompare(b),
      );

      setRoles(nextRoles);
      setAvailableTools(nextTools);
      setSelectedRoleName((current) => {
        if (current && nextRoles.includes(current)) {
          return current;
        }

        return nextRoles[0] ?? null;
      });
    } catch (error) {
      setLoadError(toErrorMessage(error, "Unable to load roles"));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadRoleTools = useCallback(async (roleName: string) => {
    setRoleToolsLoading(true);
    setRoleToolsError(null);

    try {
      const response = await fetchWithAuth(`/admin/roles/${encodeURIComponent(roleName)}/tools`);
      const payload = await jsonOrThrow<AdminRoleToolsResponse>(response);
      setToolAllowlist(coerceStringArray(payload.tools).slice().sort((a, b) => a.localeCompare(b)));
    } catch (error) {
      setRoleToolsError(toErrorMessage(error, "Unable to load role tools"));
      setToolAllowlist([]);
    } finally {
      setRoleToolsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  useEffect(() => {
    if (!selectedRoleName) {
      setToolAllowlist([]);
      setRoleToolsError(null);
      return;
    }

    setActionError(null);
    setActionMessage(null);
    void loadRoleTools(selectedRoleName);
  }, [loadRoleTools, selectedRoleName]);

  function toggleTool(toolName: string) {
    setToolAllowlist((current) => {
      if (current.includes(toolName)) {
        return current.filter((tool) => tool !== toolName);
      }

      return [...current, toolName].sort((a, b) => a.localeCompare(b));
    });
  }

  async function createRole() {
    const name = newRoleName.trim();
    if (!name) {
      setActionError("Role name is required");
      setActionMessage(null);
      return;
    }

    setCreating(true);
    setActionError(null);
    setActionMessage(null);

    try {
      const response = await fetchWithAuth("/admin/roles", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({ name }),
      });
      const payload = await jsonOrThrow<{ name: string }>(response);

      setRoles((current) => Array.from(new Set([...current, payload.name])).sort((a, b) => a.localeCompare(b)));
      setSelectedRoleName(payload.name);
      setNewRoleName("");
      setActionMessage(`Role "${payload.name}" created.`);
    } catch (error) {
      setActionError(toErrorMessage(error, "Unable to create role"));
    } finally {
      setCreating(false);
    }
  }

  async function saveRoleTools() {
    if (!selectedRoleName) {
      return;
    }

    setSaving(true);
    setActionError(null);
    setActionMessage(null);

    try {
      const response = await fetchWithAuth(`/admin/roles/${encodeURIComponent(selectedRoleName)}/tools`, {
        method: "PUT",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({ tools: toolAllowlist }),
      });
      const payload = await jsonOrThrow<AdminRoleToolsResponse>(response);
      setToolAllowlist(coerceStringArray(payload.tools).slice().sort((a, b) => a.localeCompare(b)));
      setActionMessage("Tool allowlist updated.");
    } catch (error) {
      setActionError(toErrorMessage(error, "Unable to update role tools"));
    } finally {
      setSaving(false);
    }
  }

  async function deleteRole() {
    if (!selectedRoleName) {
      return;
    }

    setConfirmDeleteOpen(false);
    setDeleting(true);
    setActionError(null);
    setActionMessage(null);

    try {
      const response = await fetchWithAuth(`/admin/roles/${encodeURIComponent(selectedRoleName)}`, {
        method: "DELETE",
      });
      await jsonOrThrow<{ ok: boolean }>(response);

      const deletedRole = selectedRoleName;
      setRoles((current) => current.filter((role) => role !== deletedRole));
      setSelectedRoleName(null);
      setToolAllowlist([]);
      setActionMessage(`Role "${deletedRole}" deleted.`);
    } catch (error) {
      setActionError(toErrorMessage(error, "Unable to delete role"));
    } finally {
      setDeleting(false);
    }
  }

  async function migrateDirectGrants() {
    setMigrating(true);
    setActionError(null);
    setActionMessage(null);

    try {
      const response = await fetchWithAuth("/admin/migrations/direct-grants", {
        method: "POST",
      });
      const payload = await jsonOrThrow<DirectGrantsMigrationResponse>(response);
      setActionMessage(formatMigrationSummary(payload));
      await loadData();
    } catch (error) {
      setActionError(toErrorMessage(error, "Unable to migrate direct grants"));
    } finally {
      setMigrating(false);
    }
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(340px,0.95fr)]">
      <RolesListPanel
        availableToolsCount={availableTools.length}
        creating={creating}
        filteredRoles={filteredRoles}
        loadError={loadError}
        loading={loading}
        newRoleName={newRoleName}
        onCreateRole={() => void createRole()}
        onNewRoleNameChange={setNewRoleName}
        onRefresh={() => void loadData()}
        onSearchChange={setSearch}
        onSelectRole={setSelectedRoleName}
        search={search}
        selectedRoleName={selectedRoleName}
      />
      <RoleToolsPanel
        actionError={actionError}
        actionMessage={actionMessage}
        availableTools={availableTools}
        confirmDeleteOpen={confirmDeleteOpen}
        deleting={deleting}
        migrating={migrating}
        onConfirmDeleteClose={() => setConfirmDeleteOpen(false)}
        onConfirmDeleteOpen={() => setConfirmDeleteOpen(true)}
        onDeleteRole={() => void deleteRole()}
        onMigrateDirectGrants={() => void migrateDirectGrants()}
        onSaveRoleTools={() => void saveRoleTools()}
        onToggleTool={toggleTool}
        roleToolsError={roleToolsError}
        roleToolsLoading={roleToolsLoading}
        saving={saving}
        selectedRoleName={selectedRoleName}
        toolAllowlist={toolAllowlist}
      />
    </div>
  );
}
