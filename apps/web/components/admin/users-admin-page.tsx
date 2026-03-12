"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Cross2Icon } from "@radix-ui/react-icons";
import * as Dialog from "@radix-ui/react-dialog";

import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";

type AdminUser = {
  id: string;
  email: string;
  display_name?: string | null;
  is_active?: boolean;
  roles?: string[];
  tools?: string[];
};

type AdminUsersResponse = {
  users: AdminUser[];
};

type AdminToolsResponse = {
  tools: string[];
};

function coerceStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function toErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

function sanitizeIdPart(value: string): string {
  return value.replace(/[^A-Za-z0-9_-]/g, "-");
}

export function UsersAdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [availableTools, setAvailableTools] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadSeqRef = useRef(0);
  const openerRef = useRef<HTMLElement | null>(null);

  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const selectedUser = useMemo(() => {
    if (!selectedUserId) return null;
    return users.find((u) => u.id === selectedUserId) ?? null;
  }, [selectedUserId, users]);

  const [toolFilter, setToolFilter] = useState("");
  const [toolAllowlist, setToolAllowlist] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setLoadError(null);

    try {
      const [usersResponse, toolsResponse] = await Promise.all([
        fetchWithAuth("/admin/users"),
        fetchWithAuth("/admin/tools"),
      ]);

      const [usersPayload, toolsPayload] = await Promise.all([
        jsonOrThrow<AdminUsersResponse>(usersResponse),
        jsonOrThrow<AdminToolsResponse>(toolsResponse),
      ]);

      if (seq !== loadSeqRef.current) return;
      setUsers(Array.isArray(usersPayload.users) ? usersPayload.users : []);
      setAvailableTools(coerceStringArray(toolsPayload.tools));
    } catch (error) {
      if (seq !== loadSeqRef.current) return;
      setLoadError(toErrorMessage(error, "Unable to load users"));
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

  const allToolNames = useMemo(() => {
    const merged = new Set<string>([...coerceStringArray(availableTools), ...coerceStringArray(toolAllowlist)]);
    return Array.from(merged).sort((a, b) => a.localeCompare(b));
  }, [availableTools, toolAllowlist]);

  const filteredToolNames = useMemo(() => {
    const needle = toolFilter.trim().toLowerCase();
    if (!needle) return allToolNames;
    return allToolNames.filter((name) => name.toLowerCase().includes(needle));
  }, [allToolNames, toolFilter]);

  const closePanel = () => {
    setSelectedUserId(null);
    setToolFilter("");
    setToolAllowlist([]);
    setSaveError(null);
    setSaving(false);
  };

  const openPanelForUser = (user: AdminUser, opener: HTMLElement | null) => {
    openerRef.current = opener;
    setSelectedUserId(user.id);
    setToolFilter("");
    setToolAllowlist(coerceStringArray(user.tools));
    setSaveError(null);
    setSaving(false);
  };

  const toggleTool = (toolName: string) => {
    setToolAllowlist((prev) => {
      if (prev.includes(toolName)) return prev.filter((t) => t !== toolName);
      return [...prev, toolName].sort((a, b) => a.localeCompare(b));
    });
  };

  const saveTools = async () => {
    if (!selectedUser) return;
    setSaving(true);
    setSaveError(null);

    try {
      const response = await fetchWithAuth(`/admin/users/${selectedUser.id}/tools`, {
        method: "PUT",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({ tools: toolAllowlist }),
      });

      await jsonOrThrow(response);

      setUsers((prev) =>
        prev.map((u) => {
          if (u.id !== selectedUser.id) return u;
          return { ...u, tools: toolAllowlist };
        })
      );
      closePanel();
    } catch (error) {
      setSaveError(toErrorMessage(error, "Unable to save tool allowlist"));
    } finally {
      setSaving(false);
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
              <button
                type="button"
                className="button shrink-0"
                disabled={loading}
                onClick={() => void loadData()}
              >
                Retry
              </button>
            </div>
          </div>
        ) : null}

        <div className="panel mt-6 overflow-hidden">
          <table className="w-full font-ui text-sm">
            <thead className="bg-surface-2 text-muted">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Email</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Roles</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Tools</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {users.length === 0 ? (
                <tr>
                  <td className="px-4 py-4 text-sm text-muted" colSpan={3}>
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

                  return (
                    <tr
                      key={user.id}
                      tabIndex={0}
                      aria-haspopup="dialog"
                      aria-label={`Edit tools for ${user.email}`}
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
                      <td className="px-4 py-3 text-muted">{roles.length ? roles.join(", ") : "-"}</td>
                      <td className="px-4 py-3 text-muted">{tools.length}</td>
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
            <Dialog.Title className="sr-only">Edit user tools</Dialog.Title>
            <Dialog.Description className="sr-only">
              Edit the selected user's authorized tools.
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
                  <button
                    type="button"
                    className="button"
                    aria-label="Close"
                  >
                    <Cross2Icon width={16} height={16} />
                  </button>
                </Dialog.Close>
              </div>

              <div className="flex-1 overflow-hidden p-4 font-ui">
                {saveError ? (
                  <p
                    role="alert"
                    aria-live="assertive"
                    className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
                  >
                    {saveError}
                  </p>
                ) : null}

                <div className={saveError ? "mt-4" : ""}>
                  <label className="text-xs font-semibold uppercase tracking-wide text-muted" htmlFor="tool-filter">
                    Tool allowlist
                  </label>
                  <input
                    id="tool-filter"
                    className="input mt-2"
                    placeholder="Filter tools..."
                    value={toolFilter}
                    onChange={(e) => setToolFilter(e.target.value)}
                  />

                  <div className="mt-3 overflow-hidden rounded-xl border border-border bg-surface">
                    <div className="max-h-[62vh] overflow-auto p-2">
                      {filteredToolNames.length === 0 ? (
                        <div className="px-2 py-3 text-sm text-muted">No matching tools.</div>
                      ) : (
                        <ul className="space-y-1">
                          {filteredToolNames.map((toolName) => {
                            const checked = toolAllowlist.includes(toolName);
                            const toolIdPart = sanitizeIdPart(toolName);
                            const userIdPart = sanitizeIdPart(selectedUser?.id ?? "unknown");
                            const inputId = `tool-${userIdPart}-${toolIdPart}`;

                            return (
                              <li key={toolName}>
                                <label
                                  className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-surface-2"
                                  htmlFor={inputId}
                                >
                                  <input
                                    id={inputId}
                                    type="checkbox"
                                    checked={checked}
                                    onChange={() => toggleTool(toolName)}
                                  />
                                  <span className="text-sm text-text">{toolName}</span>
                                </label>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                  </div>

                  <div className="mt-4 border-t border-border pt-4">
                    <button
                      type="button"
                      disabled={!selectedUser || saving}
                      className="button button-primary w-full disabled:cursor-not-allowed disabled:opacity-60"
                      onClick={() => void saveTools()}
                    >
                      {saving ? "Saving..." : "Save"}
                    </button>
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
