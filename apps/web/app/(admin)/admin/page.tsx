"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { clearAuth, useRequireAuth } from "@/components/lib/auth-store";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";

type AdminUser = {
  id: string;
  email: string;
  display_name?: string | null;
  is_active: boolean;
  roles: string[];
  tools: string[];
};

export default function AdminPage() {
  const ready = useRequireAuth();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [tools, setTools] = useState<string[]>([]);
  const [draftTools, setDraftTools] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const draftById = useMemo(() => draftTools, [draftTools]);

  const load = async () => {
    setError(null);
    try {
      const [usersResponse, toolsResponse] = await Promise.all([
        fetchWithAuth("/admin/users"),
        fetchWithAuth("/admin/tools"),
      ]);

      const usersBody = await jsonOrThrow<{ users: AdminUser[] }>(usersResponse);
      const toolsBody = await jsonOrThrow<{ tools: string[] }>(toolsResponse);
      setUsers(usersBody.users);
      setTools(toolsBody.tools);
      setDraftTools(
        Object.fromEntries(usersBody.users.map((u) => [u.id, u.tools.join(",")]))
      );
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Failed to load admin data");
    }
  };

  useEffect(() => {
    if (ready) {
      void load();
    }
  }, [ready]);

  const toggleUser = async (userId: string, nextActive: boolean) => {
    try {
      const response = await fetchWithAuth(`/admin/users/${userId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ is_active: nextActive }),
      });
      await jsonOrThrow(response);
      await load();
    } catch (toggleError) {
      setError(toggleError instanceof Error ? toggleError.message : "Failed to update user");
    }
  };

  const saveTools = async (userId: string) => {
    const draft = draftById[userId] ?? "";
    const parsed = draft
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);

    try {
      const response = await fetchWithAuth(`/admin/users/${userId}/tools`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ tools: parsed }),
      });
      await jsonOrThrow(response);
      await load();
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Failed to save tools");
    }
  };

  if (!ready) {
    return null;
  }

  return (
    <main className="min-h-dvh p-4 text-text sm:p-6">
      <div className="mx-auto w-full max-w-5xl">
        <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="font-body text-3xl leading-tight tracking-tight">Admin</h1>
            <p className="mt-1 font-ui text-sm text-muted">Manage users and tool access.</p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Link
              className="inline-flex items-center justify-center rounded-lg border border-transparent bg-accent px-3 py-2 font-ui text-sm font-medium text-white shadow-sm transition-colors hover:bg-accent/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
              href="/assistant"
            >
              Assistant
            </Link>
            <button
              className="inline-flex items-center justify-center rounded-lg border border-border bg-surface px-3 py-2 font-ui text-sm font-medium text-text shadow-sm transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
              onClick={clearAuth}
              type="button"
            >
              Logout
            </button>
          </div>
        </header>

        <section className="mt-6 rounded-xl border border-border bg-surface p-5 shadow-sm">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="font-body text-xl tracking-tight">Known tools</h2>
            <span className="font-ui text-xs text-muted">{tools.length} total</span>
          </div>
          {tools.length ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {tools.map((tool) => (
                <span
                  className="inline-flex items-center rounded-full border border-border bg-surface-2 px-2.5 py-1 font-ui text-xs text-text shadow-sm"
                  key={tool}
                >
                  {tool}
                </span>
              ))}
            </div>
          ) : (
            <p className="mt-2 font-ui text-sm text-muted">No tools registered</p>
          )}
        </section>

        <section className="mt-4 rounded-xl border border-border bg-surface p-5 shadow-sm">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
            <h2 className="font-body text-xl tracking-tight">Users</h2>
            <p className="font-ui text-sm text-muted">{users.length} users</p>
          </div>

          {error ? (
            <div
              aria-live="polite"
              className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 font-ui text-sm text-red-800"
              role="alert"
            >
              {error}
            </div>
          ) : null}

          <div className="mt-4 grid gap-3">
            {users.map((user) => {
              const inputId = `tool-allowlist-${user.id}`;
              const draftValue = draftById[user.id] ?? "";
              return (
                <article className="rounded-xl border border-border bg-surface p-4 shadow-sm" key={user.id}>
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <p className="font-ui text-base font-semibold text-text">
                        {user.display_name || user.email}
                      </p>
                      <p className="mt-0.5 truncate font-ui text-sm text-muted">{user.email}</p>
                    </div>

                    <button
                      className={
                        user.is_active
                          ? "inline-flex items-center justify-center rounded-lg border border-red-200 bg-red-50 px-3 py-2 font-ui text-sm font-medium text-red-800 shadow-sm transition-colors hover:bg-red-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300/60 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
                          : "inline-flex items-center justify-center rounded-lg border border-transparent bg-accent px-3 py-2 font-ui text-sm font-medium text-white shadow-sm transition-colors hover:bg-accent/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
                      }
                      onClick={() => toggleUser(user.id, !user.is_active)}
                      type="button"
                    >
                      {user.is_active ? "Disable" : "Enable"}
                    </button>
                  </div>

                  <p className="mt-3 font-ui text-sm text-muted">
                    Roles: {user.roles.join(", ") || "none"}
                  </p>

                  <div className="mt-4">
                    <label className="font-ui text-sm font-medium text-text" htmlFor={inputId}>
                      Tool allowlist <span className="text-muted">(comma separated)</span>
                    </label>
                    <input
                      className="mt-2 w-full rounded-lg border border-border bg-surface px-3 py-2 font-ui text-sm text-text shadow-sm outline-none placeholder:text-muted focus-visible:border-accent/60 focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
                      id={inputId}
                      onChange={(event) =>
                        setDraftTools((previous) => ({
                          ...previous,
                          [user.id]: event.target.value,
                        }))
                      }
                      placeholder="e.g. web_search, file_upload"
                      type="text"
                      value={draftValue}
                    />
                  </div>

                  <div className="mt-3 flex items-center justify-between gap-3">
                    <p className="font-ui text-xs text-muted">
                      Current: {user.tools.join(", ") || "none"}
                    </p>
                    <button
                      className="inline-flex items-center justify-center rounded-lg border border-border bg-surface px-3 py-2 font-ui text-sm font-medium text-text shadow-sm transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
                      onClick={() => saveTools(user.id)}
                      type="button"
                    >
                      Save tools
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      </div>
    </main>
  );
}
