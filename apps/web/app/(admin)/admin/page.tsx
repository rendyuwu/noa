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
    <main className="page-shell">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <h1 style={{ margin: 0 }}>Admin</h1>
        <div className="row">
          <Link className="button" href="/assistant">
            Assistant
          </Link>
          <button className="button" onClick={clearAuth} type="button">
            Logout
          </button>
        </div>
      </div>

      <section className="panel" style={{ padding: 16 }}>
        <h2 style={{ marginTop: 0 }}>Known tools</h2>
        <p className="muted" style={{ marginTop: 0 }}>
          {tools.join(", ") || "No tools registered"}
        </p>
      </section>

      <div style={{ height: 12 }} />

      <section className="panel" style={{ padding: 16 }}>
        <h2 style={{ marginTop: 0 }}>Users</h2>
        {error ? <p className="error">{error}</p> : null}
        <div style={{ display: "grid", gap: 12 }}>
          {users.map((user) => (
            <article className="panel" key={user.id} style={{ padding: 12 }}>
              <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <strong>{user.display_name || user.email}</strong>
                  <div className="muted">{user.email}</div>
                </div>
                <button
                  className={`button ${user.is_active ? "button-danger" : "button-primary"}`}
                  onClick={() => toggleUser(user.id, !user.is_active)}
                  type="button"
                >
                  {user.is_active ? "Disable" : "Enable"}
                </button>
              </div>

              <p className="muted">Roles: {user.roles.join(", ") || "none"}</p>
              <label>
                Tool allowlist (comma separated)
                <input
                  className="input"
                  value={draftById[user.id] ?? ""}
                  onChange={(event) =>
                    setDraftTools((previous) => ({
                      ...previous,
                      [user.id]: event.target.value,
                    }))
                  }
                />
              </label>
              <div style={{ height: 8 }} />
              <button className="button" onClick={() => saveTools(user.id)} type="button">
                Save tools
              </button>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
