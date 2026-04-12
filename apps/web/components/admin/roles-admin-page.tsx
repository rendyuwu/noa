"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AdminDetailModal } from "@/components/admin/admin-detail-modal";
import { AdminListLayout } from "@/components/admin/admin-list-layout";
import { Button } from "@/components/ui/button";
import { ConfirmAction, ConfirmDialog } from "@/components/lib/confirm-dialog";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toUserMessage } from "@/components/lib/error-message";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";

type AdminRole = {
  name: string;
};

type AdminRolesResponse = {
  roles: AdminRole[] | string[];
};

type AdminToolsResponse = {
  tools: string[];
};

type AdminRoleToolsResponse = {
  tools: string[];
};

type DirectGrantsMigrationResponse = Record<string, unknown>;

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

function coerceFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pickCount(payload: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = coerceFiniteNumber(payload[key]);
    if (value !== null) return value;
  }
  return null;
}

function formatDirectGrantsMigrationSummary(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "Migration completed.";
  const record = payload as Record<string, unknown>;

  const usersMigrated = pickCount(record, [
    "users_migrated",
    "usersMigrated",
    "migrated_users",
    "migratedUsers",
  ]);
  const rolesCreated = pickCount(record, [
    "roles_created",
    "rolesCreated",
    "created_roles",
    "createdRoles",
  ]);
  const rolesReused = pickCount(record, [
    "roles_reused",
    "rolesReused",
    "reused_roles",
    "reusedRoles",
  ]);

  const parts: string[] = [];
  if (usersMigrated !== null) {
    parts.push(`${usersMigrated} user${usersMigrated === 1 ? "" : "s"} migrated`);
  }
  if (rolesCreated !== null) {
    parts.push(`${rolesCreated} role${rolesCreated === 1 ? "" : "s"} created`);
  }
  if (rolesReused !== null) {
    parts.push(`${rolesReused} role${rolesReused === 1 ? "" : "s"} reused`);
  }

  if (parts.length === 0) return "Migration completed.";
  return `Migration complete: ${parts.join("; ")}.`;
}

export function RolesAdminPage() {
  const [roles, setRoles] = useState<string[]>([]);
  const [availableTools, setAvailableTools] = useState<string[]>([]);
  const [roleToolCountsByName, setRoleToolCountsByName] = useState<Record<string, number>>({});
  const [roleToolsByName, setRoleToolsByName] = useState<Record<string, string[]>>({});

  const [migrationBusy, setMigrationBusy] = useState(false);
  const [migrationError, setMigrationError] = useState<string | null>(null);
  const [migrationSummary, setMigrationSummary] = useState<string | null>(null);
  const [migrationOpen, setMigrationOpen] = useState(false);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadSeqRef = useRef(0);
  const roleToolCountVersionRef = useRef<Record<string, number>>({});

  const bumpRoleToolCountVersion = useCallback((roleName: string) => {
    roleToolCountVersionRef.current[roleName] = (roleToolCountVersionRef.current[roleName] ?? 0) + 1;
  }, []);

  const loadRoleToolCounts = useCallback(async (roleNames: string[], seq: number) => {
    const requestedVersions = Object.fromEntries(
      roleNames.map((roleName) => [roleName, roleToolCountVersionRef.current[roleName] ?? 0])
    );

    const roleToolResponses = await Promise.allSettled(
      roleNames.map(async (roleName) => {
        const response = await fetchWithAuth(`/admin/roles/${encodeURIComponent(roleName)}/tools`);
        const payload = await jsonOrThrow<AdminRoleToolsResponse>(response);
        return [roleName, coerceStringArray(payload.tools).length] as const;
      })
    );

    if (seq !== loadSeqRef.current) return;

    setRoleToolCountsByName((prev) => {
      const next = { ...prev };
      for (const result of roleToolResponses) {
        if (result.status === "fulfilled") {
          const [roleName, toolCount] = result.value;
          if ((roleToolCountVersionRef.current[roleName] ?? 0) === requestedVersions[roleName]) {
            next[roleName] = toolCount;
          }
        }
      }
      return next;
    });
  }, []);

  const loadData = useCallback(async () => {
    const seq = ++loadSeqRef.current;
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

      if (seq !== loadSeqRef.current) return;

      const roleNames = coerceRoleNames(rolesPayload.roles);
      const uniqueRoleNames = Array.from(new Set(roleNames)).sort((a, b) => a.localeCompare(b));
      setRoles(uniqueRoleNames);
      setAvailableTools(coerceStringArray(toolsPayload.tools).slice().sort((a, b) => a.localeCompare(b)));
      setRoleToolCountsByName((prev) => {
        const next: Record<string, number> = {};
        for (const roleName of uniqueRoleNames) {
          if (typeof prev[roleName] === "number") {
            next[roleName] = prev[roleName];
          }
        }
        return next;
      });
      setRoleToolsByName((prev) => {
        const next: Record<string, string[]> = {};
        for (const roleName of uniqueRoleNames) {
          if (prev[roleName]) {
            next[roleName] = prev[roleName];
          }
        }
        return next;
      });

      void loadRoleToolCounts(uniqueRoleNames, seq);
    } catch (error) {
      if (seq !== loadSeqRef.current) return;
      setLoadError(toUserMessage(error, "Unable to load roles"));
    } finally {
      if (seq === loadSeqRef.current) {
        setLoading(false);
      }
    }
  }, [loadRoleToolCounts]);

  useEffect(() => {
    void loadData();
    return () => {
      loadSeqRef.current += 1;
    };
  }, [loadData]);

  const migrateLegacyDirectGrants = useCallback(async () => {
    setMigrationBusy(true);
    setMigrationError(null);
    setMigrationSummary(null);

    try {
      const response = await fetchWithAuth("/admin/migrations/direct-grants", {
        method: "POST",
      });
      const payload = await jsonOrThrow<DirectGrantsMigrationResponse>(response);
      setMigrationSummary(formatDirectGrantsMigrationSummary(payload));
      setMigrationOpen(false);
      void loadData();
    } catch (error) {
      setMigrationError(toUserMessage(error, "Unable to migrate legacy direct grants"));
    } finally {
      setMigrationBusy(false);
    }
  }, [loadData]);

  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const resetCreate = () => {
    setCreateName("");
    setCreateError(null);
    setCreating(false);
  };

  const createRole = async () => {
    const name = createName.trim();
    if (!name) {
      setCreateError("Role name is required");
      return;
    }

    setCreating(true);
    setCreateError(null);

    try {
      const response = await fetchWithAuth("/admin/roles", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({ name }),
      });

      await jsonOrThrow(response);
      setRoles((prev) => Array.from(new Set([...prev, name])).sort((a, b) => a.localeCompare(b)));
      setRoleToolCountsByName((prev) => ({ ...prev, [name]: 0 }));
      setRoleToolsByName((prev) => ({ ...prev, [name]: [] }));
      setCreateOpen(false);
      resetCreate();
    } catch (error) {
      setCreateError(toUserMessage(error, "Unable to create role"));
    } finally {
      setCreating(false);
    }
  };

  const panelSeqRef = useRef(0);
  const selectedRoleNameRef = useRef<string | null>(null);

  const [selectedRoleName, setSelectedRoleName] = useState<string | null>(null);
  const [roleToolsLoading, setRoleToolsLoading] = useState(false);
  const [roleToolsError, setRoleToolsError] = useState<string | null>(null);

  const [toolFilter, setToolFilter] = useState("");
  const [toolAllowlist, setToolAllowlist] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [deleteSaving, setDeleteSaving] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const panelStillMatches = useCallback((seq: number, roleName: string) => {
    return panelSeqRef.current === seq && selectedRoleNameRef.current === roleName;
  }, []);

  const closePanel = () => {
    panelSeqRef.current += 1;
    selectedRoleNameRef.current = null;

    setSelectedRoleName(null);
    setToolFilter("");
    setToolAllowlist([]);
    setRoleToolsLoading(false);
    setRoleToolsError(null);
    setSaving(false);
    setSaveError(null);
    setDeleteSaving(false);
    setDeleteError(null);
  };

  const loadRoleTools = useCallback(async (roleName: string, seq: number) => {
    setRoleToolsLoading(true);
    setRoleToolsError(null);

    try {
      const response = await fetchWithAuth(`/admin/roles/${encodeURIComponent(roleName)}/tools`);
      const payload = await jsonOrThrow<AdminRoleToolsResponse>(response);

      if (!panelStillMatches(seq, roleName)) return;

      const tools = coerceStringArray(payload.tools).slice().sort((a, b) => a.localeCompare(b));
      bumpRoleToolCountVersion(roleName);
      setToolAllowlist(tools);
      setRoleToolCountsByName((prev) => ({ ...prev, [roleName]: tools.length }));
      setRoleToolsByName((prev) => ({ ...prev, [roleName]: tools }));
    } catch (error) {
      if (!panelStillMatches(seq, roleName)) return;
      setRoleToolsError(toUserMessage(error, "Unable to load role tools"));
      setToolAllowlist([]);
    } finally {
      if (panelStillMatches(seq, roleName)) {
        setRoleToolsLoading(false);
      }
    }
  }, [bumpRoleToolCountVersion, panelStillMatches]);

  const openPanelForRole = (roleName: string, _opener: HTMLElement | null) => {
    panelSeqRef.current += 1;
    const seq = panelSeqRef.current;

    selectedRoleNameRef.current = roleName;
    setSelectedRoleName(roleName);
    setToolFilter("");
    setToolAllowlist(roleToolsByName[roleName] ?? []);
    setRoleToolsError(null);
    setSaveError(null);
    setDeleteError(null);
    setSaving(false);
    setDeleteSaving(false);

    void loadRoleTools(roleName, seq);
  };

  const toggleTool = (toolName: string) => {
    setToolAllowlist((prev) => {
      if (prev.includes(toolName)) return prev.filter((t) => t !== toolName);
      return [...prev, toolName].sort((a, b) => a.localeCompare(b));
    });
  };

  const allToolNames = useMemo(() => {
    const merged = new Set<string>([...coerceStringArray(availableTools), ...coerceStringArray(toolAllowlist)]);
    return Array.from(merged).sort((a, b) => a.localeCompare(b));
  }, [availableTools, toolAllowlist]);

  const filteredToolNames = useMemo(() => {
    const needle = toolFilter.trim().toLowerCase();
    if (!needle) return allToolNames;
    return allToolNames.filter((name) => name.toLowerCase().includes(needle));
  }, [allToolNames, toolFilter]);

  const saveRoleTools = async () => {
    if (!selectedRoleName) return;

    const seq = panelSeqRef.current;
    const roleName = selectedRoleName;
    const requestedTools = toolAllowlist.slice();

    setSaving(true);
    setSaveError(null);

    try {
      const response = await fetchWithAuth(`/admin/roles/${encodeURIComponent(roleName)}/tools`, {
        method: "PUT",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({ tools: requestedTools }),
      });

      await jsonOrThrow(response);

      bumpRoleToolCountVersion(roleName);
      setRoleToolCountsByName((prev) => ({ ...prev, [roleName]: requestedTools.length }));
      setRoleToolsByName((prev) => ({ ...prev, [roleName]: requestedTools }));
      if (panelStillMatches(seq, roleName)) {
        closePanel();
      }
    } catch (error) {
      if (panelStillMatches(seq, roleName)) {
        setSaveError(toUserMessage(error, "Unable to save role tools"));
      }
    } finally {
      if (panelStillMatches(seq, roleName)) {
        setSaving(false);
      }
    }
  };

  const deleteRole = async () => {
    if (!selectedRoleName) return;

    const seq = panelSeqRef.current;
    const roleName = selectedRoleName;

    setDeleteSaving(true);
    setDeleteError(null);

    try {
      const response = await fetchWithAuth(`/admin/roles/${encodeURIComponent(roleName)}`, {
        method: "DELETE",
      });

      await jsonOrThrow(response);

      setRoles((prev) => prev.filter((name) => name !== roleName));
      bumpRoleToolCountVersion(roleName);
      setRoleToolCountsByName((prev) => {
        const next = { ...prev };
        delete next[roleName];
        return next;
      });
      setRoleToolsByName((prev) => {
        const next = { ...prev };
        delete next[roleName];
        return next;
      });

      if (panelStillMatches(seq, roleName)) {
        closePanel();
      }
    } catch (error) {
      if (panelStillMatches(seq, roleName)) {
        setDeleteError(toUserMessage(error, "Unable to delete role"));
      }
    } finally {
      if (panelStillMatches(seq, roleName)) {
        setDeleteSaving(false);
      }
    }
  };

  return (
    <>
      <AdminListLayout
        title="Roles"
        description="Manage tool sets and assign them to users."
        loading={loading}
        error={loadError}
        onRetry={() => void loadData()}
        empty={!loading && !loadError && roles.length === 0}
        emptyTitle="No roles yet"
        emptyDescription="Create a role to define shared access and tool grants."
        actions={
          <>
            <Button
              disabled={loading || migrationBusy}
              onClick={() => { setMigrationError(null); setMigrationOpen(true); }}
              size="sm"
              variant="outline"
            >
              Migrate legacy direct grants
            </Button>
            <Button disabled={loading} onClick={() => void loadData()} size="sm" variant="outline">
              Refresh
            </Button>
            <Button onClick={() => setCreateOpen(true)} size="sm" variant="default">
              Add role
            </Button>
          </>
        }
      >
        {migrationSummary ? (
          <div role="status" aria-live="polite"
            className="rounded-xl border border-success/25 bg-success/10 px-3 py-2 font-sans text-sm text-success mb-4">
            {migrationSummary}
          </div>
        ) : null}

        <div className="panel overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full border-collapse text-left">
              <thead className="bg-accent/40 text-accent-foreground">
                <tr>
                  <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                    Role
                  </th>
                  <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                    Tools
                  </th>
                </tr>
              </thead>
              <tbody>
                {roles.map((roleName) => {
                  const toolCount = roleToolCountsByName[roleName];
                  const selected = selectedRoleName === roleName;
                  const toolSummary =
                    typeof toolCount === "number"
                      ? `${toolCount} tool${toolCount === 1 ? "" : "s"} assigned`
                      : "—";

                  return (
                    <tr
                      key={roleName}
                      aria-selected={selected}
                      className={selected ? "border-b border-border bg-accent/40 last:border-b-0" : "border-b border-border bg-card last:border-b-0"}
                    >
                      <th scope="row" className="px-4 py-3 align-top text-left font-normal">
                        <button
                          type="button"
                          className="group block rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
                          aria-label={`Manage ${roleName}`}
                          onClick={() => openPanelForRole(roleName, null)}
                        >
                          <span className="block text-sm font-medium text-foreground group-hover:underline">{roleName}</span>
                        </button>
                      </th>
                      <td className="px-4 py-3 align-top">
                        <span className="text-sm text-foreground">{toolSummary}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </AdminListLayout>

      {/* Add role create dialog */}
      <Dialog open={createOpen} onOpenChange={(open) => { if (open) resetCreate(); setCreateOpen(open); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Add role</DialogTitle>
            <DialogDescription>Create a new role name to assign tools and grant access.</DialogDescription>
          </DialogHeader>
          <div className="editorial-subpanel font-sans">
            <label className="block text-sm font-medium text-foreground" htmlFor="role-name">Role name</label>
            <input id="role-name" className="input mt-2 w-full" placeholder="e.g. support" value={createName} disabled={creating} onChange={(e) => setCreateName(e.target.value)} />
            {createError ? (
              <p
                role="alert"
                aria-live="assertive"
                className="mt-4 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {createError}
              </p>
            ) : null}
            <div className="mt-5 flex items-center justify-end gap-2 border-t border-border pt-4">
              <Button disabled={creating} onClick={() => setCreateOpen(false)} size="sm" variant="outline">Cancel</Button>
              <Button disabled={creating} onClick={() => void createRole()} size="sm" variant="default">{creating ? "Saving..." : "Save"}</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Migration confirm dialog */}
      <ConfirmDialog
        open={migrationOpen}
        onOpenChange={setMigrationOpen}
        title="Migrate legacy direct grants?"
        description="Convert any remaining per-user direct tool grants into role-based grants. Safe to run multiple times."
        confirmLabel="Migrate"
        confirmBusyLabel="Migrating..."
        confirmVariant="primary"
        cancelLabel="Close"
        busy={migrationBusy}
        error={migrationError}
        onConfirm={migrateLegacyDirectGrants}
      />

      {/* Role detail modal */}
      <AdminDetailModal
        open={selectedRoleName !== null}
        onOpenChange={(open) => { if (!open) closePanel(); }}
        title={selectedRoleName ?? "Role"}
        subtitle="Manage tool assignments"
        size="md"
        footer={
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <ConfirmAction
              title="Delete role?"
              description={selectedRoleName ? `This permanently deletes the ${selectedRoleName} role.` : "This permanently deletes this role."}
              confirmLabel="Delete role"
              confirmBusyLabel="Deleting..."
              confirmVariant="danger"
              busy={deleteSaving}
              error={deleteError}
              onConfirm={() => void deleteRole()}
              trigger={({ open, disabled }) => (
                <Button
                  className="w-full sm:w-auto"
                  disabled={!selectedRoleName || disabled || saving || roleToolsLoading}
                  onClick={() => { setDeleteError(null); open(); }}
                  variant="destructive"
                >
                  Delete role
                </Button>
              )}
            />
            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <DialogClose asChild>
                <Button disabled={saving || deleteSaving} variant="outline">
                  Cancel
                </Button>
              </DialogClose>
              <Button
                disabled={!selectedRoleName || saving || roleToolsLoading}
                onClick={() => void saveRoleTools()}
                variant="default"
              >
                {saving ? "Saving..." : "Save"}
              </Button>
            </div>
          </div>
        }
      >
        {/* Error alerts */}
        {roleToolsError ? (
          <p role="alert" aria-live="assertive"
            className="mb-3 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {roleToolsError}
          </p>
        ) : null}
        {saveError ? (
          <p role="alert" aria-live="assertive"
            className="mb-3 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {saveError}
          </p>
        ) : null}
        {deleteError ? (
          <p role="alert" aria-live="assertive"
            className="mb-3 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {deleteError}
          </p>
        ) : null}

        {/* Tool filter */}
        <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground" htmlFor="role-tool-filter">
          Tools
        </label>
        <input
          id="role-tool-filter"
          className="input mt-2 w-full"
          placeholder="Filter tools..."
          value={toolFilter}
          disabled={roleToolsLoading}
          onChange={(e) => setToolFilter(e.target.value)}
        />

        {/* Tool checkbox list */}
        <div className="editorial-subpanel mt-3 p-4">
          <div className="max-h-[50vh] overflow-y-auto p-2">
            {roleToolsLoading ? (
              <div className="px-2 py-3 text-sm text-muted-foreground">Loading role tools...</div>
            ) : filteredToolNames.length === 0 ? (
              <div className="px-2 py-3 text-sm text-muted-foreground">No matching tools.</div>
            ) : (
              <ul className="space-y-1">
                {filteredToolNames.map((toolName) => {
                  const checked = toolAllowlist.includes(toolName);
                  const toolIdPart = sanitizeIdPart(toolName);
                  const roleIdPart = sanitizeIdPart(selectedRoleName ?? "unknown");
                  const inputId = `role-tool-${roleIdPart}-${toolIdPart}`;
                  return (
                    <li key={toolName}>
                      <label className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-accent" htmlFor={inputId}>
                        <input id={inputId} type="checkbox" checked={checked} onChange={() => toggleTool(toolName)} />
                        <span className="text-sm text-foreground">{toolName}</span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>

        {/* Danger zone */}
        <div className="danger-zone mt-8">
          <div className="danger-zone-label">Danger zone</div>
          <p className="danger-zone-copy">Delete this role and remove its tool assignments.</p>
        </div>
      </AdminDetailModal>
    </>
  );
}
