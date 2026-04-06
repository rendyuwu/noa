"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import * as Dialog from "@radix-ui/react-dialog";
import { Cross2Icon } from "@radix-ui/react-icons";

import { AdminTableEmptyState, AdminTableLoadingRows } from "@/components/admin/admin-table-empty-state";
import { Button } from "@/components/ui/button";
import { ConfirmAction, ConfirmDialog } from "@/components/lib/confirm-dialog";
import { toUserMessage } from "@/components/lib/error-message";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";
import { ScrollArea } from "@/components/lib/scroll-area";

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
  const [roleToolsByName, setRoleToolsByName] = useState<Record<string, string[]>>({});

  const [migrationBusy, setMigrationBusy] = useState(false);
  const [migrationError, setMigrationError] = useState<string | null>(null);
  const [migrationSummary, setMigrationSummary] = useState<string | null>(null);
  const [migrationOpen, setMigrationOpen] = useState(false);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadSeqRef = useRef(0);

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
      setRoles(Array.from(new Set(roleNames)).sort((a, b) => a.localeCompare(b)));
      setAvailableTools(coerceStringArray(toolsPayload.tools).slice().sort((a, b) => a.localeCompare(b)));
    } catch (error) {
      if (seq !== loadSeqRef.current) return;
      setLoadError(toUserMessage(error, "Unable to load roles"));
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
      setCreateOpen(false);
      resetCreate();
    } catch (error) {
      setCreateError(toUserMessage(error, "Unable to create role"));
    } finally {
      setCreating(false);
    }
  };

  const panelSeqRef = useRef(0);
  const openerRef = useRef<HTMLElement | null>(null);
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

  const panelStillMatches = (seq: number, roleName: string) => {
    return panelSeqRef.current === seq && selectedRoleNameRef.current === roleName;
  };

  const closePanel = () => {
    panelSeqRef.current += 1;
    openerRef.current = null;
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
      setToolAllowlist(tools);
      setRoleToolsByName((prev) => ({ ...prev, [roleName]: tools }));
    } catch (error) {
      if (!panelStillMatches(seq, roleName)) return;
      setRoleToolsError(toUserMessage(error, "Unable to load role tools"));
      setToolAllowlist([]);
    } finally {
      if (!panelStillMatches(seq, roleName)) return;
      setRoleToolsLoading(false);
    }
  }, []);

  const openPanelForRole = (roleName: string, opener: HTMLElement | null) => {
    panelSeqRef.current += 1;
    const seq = panelSeqRef.current;

    openerRef.current = opener;
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
      <Dialog.Root
        open={createOpen}
        onOpenChange={(open) => {
          if (open) {
            resetCreate();
          }
          setCreateOpen(open);
        }}
      >
        <main className="min-h-dvh bg-background p-6">
          <div className="flex items-end justify-between gap-3">
            <div>
              <h1 className="text-2xl font-semibold">Roles</h1>
              <p className="mt-1 font-sans text-sm text-muted-foreground">Manage tool sets and assign them to users.</p>
            </div>

            <div className="flex items-center gap-2">
              <Button
                disabled={loading || migrationBusy}
                onClick={() => {
                  setMigrationError(null);
                  setMigrationOpen(true);
                }}
                size="sm"
              >
                Migrate legacy direct grants
              </Button>
              <Button disabled={loading} onClick={() => void loadData()} size="sm">
                Refresh
              </Button>
              <Dialog.Trigger asChild>
                <Button variant="default" size="sm">
                  Add role
                </Button>
              </Dialog.Trigger>
            </div>
          </div>

          {loadError ? (
            <div
              role="alert"
              aria-live="assertive"
              className="mt-4 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 font-sans text-sm text-destructive"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">{loadError}</div>
                <Button className="shrink-0" disabled={loading} onClick={() => void loadData()} size="sm">
                  Retry
                </Button>
              </div>
            </div>
          ) : null}

          {migrationSummary ? (
            <div
              role="status"
              aria-live="polite"
              className="mt-4 rounded-xl border border-success/25 bg-success/10 px-3 py-2 font-sans text-sm text-success"
            >
              {migrationSummary}
            </div>
          ) : null}

          <div className="panel mt-6 overflow-hidden">
            <table className="w-full font-sans text-sm">
              <thead className="bg-accent text-accent-foreground">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Role</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Tools</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {roles.length === 0 ? (
                  loading ? (
                    <AdminTableLoadingRows columns={3} />
                  ) : loadError ? (
                    <tr>
                      <td className="px-4 py-4 text-sm text-muted-foreground" colSpan={3}>
                        Unable to load roles.
                      </td>
                    </tr>
                  ) : (
                    <AdminTableEmptyState
                      columns={3}
                      title="No roles yet"
                      description="Create a role to define shared access and tool grants."
                    />
                  )
                ) : (
                  roles.map((roleName) => {
                    const cachedCount = roleToolsByName[roleName]?.length;

                    return (
                      <tr
                        key={roleName}
                        tabIndex={0}
                        aria-haspopup="dialog"
                        aria-label={`Manage ${roleName}`}
                        className="cursor-pointer transition-colors hover:bg-primary/60 focus-visible:bg-primary/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent/40"
                        onClick={(event) => openPanelForRole(roleName, event.currentTarget)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
                            event.preventDefault();
                            openPanelForRole(roleName, event.currentTarget);
                          }
                        }}
                      >
                        <td className="px-4 py-3 text-foreground">
                          <div className="font-medium text-foreground">{roleName}</div>
                        </td>
                        <td className="px-4 py-3 text-muted-foreground">
                          {typeof cachedCount === "number" ? cachedCount : "-"}
                        </td>
                        <td className="px-4 py-3 text-primary font-medium">Manage</td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30 opacity-0 transition-opacity data-[state=open]:opacity-100 data-[state=closed]:opacity-0" />
            <Dialog.Content className="fixed top-1/2 left-1/2 z-50 w-[min(92vw,460px)] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-2xl border border-border bg-background shadow-xl outline-none">
              <div className="flex items-start justify-between gap-3 border-b border-border bg-card/50 px-5 py-4">
                <div className="min-w-0">
                  <Dialog.Title className="text-lg font-semibold text-foreground">Add role</Dialog.Title>
                  <Dialog.Description className="mt-1 font-sans text-sm text-muted-foreground">
                    Create a new role name to assign tools and grant access.
                  </Dialog.Description>
                </div>
                <Dialog.Close asChild>
                  <Button aria-label="Close" className="text-muted-foreground hover:text-foreground" size="icon">
                    <Cross2Icon width={18} height={18} />
                  </Button>
                </Dialog.Close>
              </div>

              <div className="px-5 py-4 font-sans">
                <label className="block text-sm font-medium text-foreground" htmlFor="role-name">
                  Role name
                </label>
                <input
                  id="role-name"
                  className="input mt-2"
                  placeholder="e.g. support"
                  value={createName}
                  disabled={creating}
                  onChange={(e) => setCreateName(e.target.value)}
                />

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
                  <Dialog.Close asChild>
                    <Button disabled={creating} size="sm">
                      Cancel
                    </Button>
                  </Dialog.Close>
                  <Button disabled={creating} onClick={() => void createRole()} size="sm" variant="default">
                    {creating ? "Saving..." : "Save"}
                  </Button>
                </div>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </main>
      </Dialog.Root>

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

      <Dialog.Root
        open={selectedRoleName !== null}
        onOpenChange={(open) => {
          if (!open) closePanel();
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30 opacity-0 transition-opacity data-[state=open]:opacity-100 data-[state=closed]:opacity-0" />
          <Dialog.Content
            className={[
              "fixed inset-y-0 right-0 z-50 w-[30rem] max-w-[92vw]",
              "border-border border-l bg-background shadow-md",
              "transition-transform duration-200 ease-out",
              "data-[state=open]:translate-x-0 data-[state=closed]:translate-x-full",
              "outline-none",
            ].join(" ")}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              openerRef.current?.focus();
            }}
          >
            <Dialog.Title className="sr-only">Manage role</Dialog.Title>
            <Dialog.Description className="sr-only">Edit tool assignments for the selected role.</Dialog.Description>

            <div className="flex h-full flex-col">
              <div className="flex items-start justify-between gap-3 border-b border-border bg-card px-4 py-4">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-foreground">{selectedRoleName ?? "Role"}</div>
                  <div className="mt-0.5 text-xs text-muted-foreground">Manage tool assignments</div>
                </div>
                <Dialog.Close asChild>
                  <Button aria-label="Close" size="icon">
                    <Cross2Icon width={16} height={16} />
                  </Button>
                </Dialog.Close>
              </div>


              <ScrollArea className="flex-1 min-h-0 font-sans" viewportClassName="h-full p-4">
                {roleToolsError ? (
                  <p
                    role="alert"
                    aria-live="assertive"
                    className="mb-3 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                  >
                    {roleToolsError}
                  </p>
                ) : null}

                {saveError ? (
                  <p
                    role="alert"
                    aria-live="assertive"
                    className="mb-3 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                  >
                    {saveError}
                  </p>
                ) : null}

                {deleteError ? (
                  <p
                    role="alert"
                    aria-live="assertive"
                    className="mb-3 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive"
                  >
                    {deleteError}
                  </p>
                ) : null}

                <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground" htmlFor="role-tool-filter">
                  Tools
                </label>
                <input
                  id="role-tool-filter"
                  className="input mt-2"
                  placeholder="Filter tools..."
                  value={toolFilter}
                  disabled={roleToolsLoading}
                  onChange={(e) => setToolFilter(e.target.value)}
                />


                <div className="mt-3 overflow-hidden rounded-xl border border-border bg-card">
                  <ScrollArea className="w-full" horizontalScrollbar viewportClassName="max-h-[62vh] p-2">
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
                              <label
                                className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-accent"
                                htmlFor={inputId}
                              >
                                <input
                                  id={inputId}
                                  type="checkbox"
                                  checked={checked}
                                  onChange={() => toggleTool(toolName)}
                                />
                                <span className="text-sm text-foreground">{toolName}</span>
                              </label>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </ScrollArea>
                </div>

                <div className="mt-4 border-t border-border pt-4">
                  <Button
                    className="w-full"
                    disabled={!selectedRoleName || saving || roleToolsLoading}
                    onClick={() => void saveRoleTools()}
                    variant="default"
                  >
                    {saving ? "Saving..." : "Save"}
                  </Button>
                </div>


                <div className="danger-zone mt-6">
                  <div className="danger-zone-label text-xs font-semibold uppercase tracking-wide">Danger zone</div>
                  <p className="danger-zone-copy mt-1 text-sm">Delete this role and remove its tool assignments.</p>
                  <ConfirmAction
                    title="Delete role?"
                    description={
                      selectedRoleName
                        ? `This permanently deletes the ${selectedRoleName} role.`
                        : "This permanently deletes this role."
                    }
                    confirmLabel="Delete role"
                    confirmBusyLabel="Deleting..."
                    confirmVariant="danger"
                    busy={deleteSaving}
                    error={deleteError}
                    onConfirm={() => void deleteRole()}
                    trigger={({ open, disabled }) => (
                      <Button
                        className="mt-3 w-full"
                        disabled={!selectedRoleName || disabled || saving || roleToolsLoading}
                        onClick={() => {
                          setDeleteError(null);
                          open();
                        }}
                        variant="destructive"
                      >
                        Delete role
                      </Button>
                    )}
                  />

                </div>
              </ScrollArea>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
