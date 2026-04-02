"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Shield, Sparkles, Trash2, Wand2 } from "lucide-react";

import { fetchWithAuth, jsonOrThrow } from "@/components/lib/http/fetch-client";
import { toErrorMessage } from "@/components/lib/http/error-message";

type AdminRolesResponse = {
  roles: Array<{ name: string }> | string[];
};

type AdminToolsResponse = {
  tools: string[];
};

type AdminRoleToolsResponse = {
  tools: string[];
};

type DirectGrantsMigrationResponse = Record<string, unknown>;

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

function coerceFiniteNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pickCount(payload: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = coerceFiniteNumber(payload[key]);
    if (value !== null) {
      return value;
    }
  }

  return null;
}

function formatMigrationSummary(payload: DirectGrantsMigrationResponse) {
  const usersMigrated = pickCount(payload, ["users_migrated", "usersMigrated"]);
  const rolesCreated = pickCount(payload, ["roles_created", "rolesCreated"]);
  const rolesReused = pickCount(payload, ["roles_reused", "rolesReused"]);

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

  return parts.length > 0 ? `Migration complete: ${parts.join("; ")}.` : "Migration completed.";
}

function toolBadgeClass(selected: boolean) {
  return selected
    ? "border-accent bg-accent text-accent-foreground"
    : "border-border bg-bg text-text hover:border-accent/40 hover:bg-surface-2";
}

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

    const confirmed = window.confirm(`Delete role "${selectedRoleName}"?`);
    if (!confirmed) {
      return;
    }

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
            onClick={() => void loadData()}
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
              onChange={(event) => setNewRoleName(event.target.value)}
              placeholder="billing-ops"
              className="min-w-0 flex-1 rounded-2xl border border-border bg-surface px-4 py-3 text-sm text-text outline-none transition focus:border-accent"
            />
            <button
              type="button"
              onClick={() => void createRole()}
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
            onChange={(event) => setSearch(event.target.value)}
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
                onClick={() => setSelectedRoleName(role)}
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
                      {availableTools.length} available tool{availableTools.length === 1 ? "" : "s"}
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

      <section className="rounded-3xl border border-border bg-surface p-5 shadow-soft">
        {selectedRoleName ? (
          <>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">Selected role</p>
                <h2 className="mt-2 truncate text-2xl font-semibold tracking-[-0.02em] text-text">
                  {selectedRoleName}
                </h2>
                <p className="mt-1 font-ui text-sm text-muted">
                  Update the allowlist for backend tool access granted through this role.
                </p>
              </div>
              <Wand2 className="mt-1 size-5 shrink-0 text-accent" />
            </div>

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
                <Shield className="size-4 text-accent" />
                <h3 className="text-base font-semibold text-text">Tool allowlist</h3>
              </div>
              <p className="mt-2 font-ui text-sm text-muted">
                Toggle the tools this role can use. The list is backed by `/admin/tools` and saved through `/admin/roles/:name/tools`.
              </p>
            </div>

            {roleToolsError ? (
              <div role="alert" className="mt-5 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 font-ui text-sm text-red-700">
                {roleToolsError}
              </div>
            ) : null}

            <div className="mt-5 flex flex-wrap gap-2">
              {roleToolsLoading ? (
                <div className="rounded-2xl border border-dashed border-border px-4 py-6 font-ui text-sm text-muted">
                  Loading role tools…
                </div>
              ) : availableTools.length > 0 ? (
                availableTools.map((toolName) => {
                  const selected = toolAllowlist.includes(toolName);
                  return (
                    <button
                      key={toolName}
                      type="button"
                      onClick={() => toggleTool(toolName)}
                      className={[
                        "rounded-full border px-3 py-2 font-ui text-sm transition",
                        toolBadgeClass(selected),
                      ].join(" ")}
                    >
                      {toolName}
                    </button>
                  );
                })
              ) : (
                <div className="rounded-2xl border border-dashed border-border px-4 py-6 font-ui text-sm text-muted">
                  No tools are available yet.
                </div>
              )}
            </div>

            <div className="mt-6 flex flex-col gap-3">
              <button
                type="button"
                onClick={() => void saveRoleTools()}
                disabled={saving || roleToolsLoading}
                className="inline-flex items-center justify-center gap-2 rounded-2xl bg-accent px-4 py-3 font-ui text-sm font-semibold text-accent-foreground disabled:opacity-70"
              >
                <Shield className="size-4" />
                {saving ? "Saving allowlist…" : "Save allowlist"}
              </button>
              <button
                type="button"
                onClick={() => void migrateDirectGrants()}
                disabled={migrating}
                className="inline-flex items-center justify-center gap-2 rounded-2xl border border-border bg-bg px-4 py-3 font-ui text-sm font-medium text-text transition hover:bg-surface-2 disabled:opacity-70"
              >
                <Sparkles className="size-4" />
                {migrating ? "Migrating…" : "Migrate legacy direct grants"}
              </button>
              <button
                type="button"
                onClick={() => void deleteRole()}
                disabled={deleting}
                className="inline-flex items-center justify-center gap-2 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 font-ui text-sm font-medium text-red-700 transition hover:bg-red-100 disabled:opacity-70"
              >
                <Trash2 className="size-4" />
                {deleting ? "Deleting…" : "Delete role"}
              </button>
            </div>
          </>
        ) : (
          <div className="flex min-h-[24rem] items-center justify-center rounded-2xl border border-dashed border-border px-4 py-8 text-center font-ui text-sm text-muted">
            Select a role to manage its tool allowlist and migration helpers.
          </div>
        )}
      </section>
    </div>
  );
}
