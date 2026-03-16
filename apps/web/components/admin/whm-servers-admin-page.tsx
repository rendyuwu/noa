"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import * as Dialog from "@radix-ui/react-dialog";
import { Cross2Icon } from "@radix-ui/react-icons";

import { Button } from "@/components/lib/button";
import { toUserMessage } from "@/components/lib/error-message";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";

type WhmServer = {
  id: string;
  name: string;
  base_url: string;
  api_username: string;
  verify_ssl: boolean;
  created_at?: string;
  updated_at?: string;
};

type ListWhmServersResponse = {
  servers: WhmServer[];
};

type CreateWhmServerResponse = {
  server: WhmServer;
};

type ValidateWhmServerResponse = {
  ok: boolean;
  error_code?: string | null;
  message: string;
};

function formatTimestamp(value: unknown): string {
  if (typeof value !== "string" || !value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "Z");
}

export function WhmServersAdminPage() {
  const [servers, setServers] = useState<WhmServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiUsername, setApiUsername] = useState("");
  const [apiToken, setApiToken] = useState("");
  const [verifySsl, setVerifySsl] = useState(true);

  const [validateBusyId, setValidateBusyId] = useState<string | null>(null);
  const [validateResultById, setValidateResultById] = useState<Record<string, ValidateWhmServerResponse>>({});
  const [deleteBusyId, setDeleteBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const loadSeqRef = useRef(0);

  const loadServers = useCallback(async () => {
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setLoadError(null);

    try {
      const response = await fetchWithAuth("/admin/whm/servers");
      const payload = await jsonOrThrow<ListWhmServersResponse>(response);
      if (seq !== loadSeqRef.current) return;
      setServers(Array.isArray(payload.servers) ? payload.servers : []);
    } catch (error) {
      if (seq !== loadSeqRef.current) return;
      setLoadError(toUserMessage(error, "Unable to load WHM servers"));
    } finally {
      if (seq !== loadSeqRef.current) return;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadServers();
    return () => {
      loadSeqRef.current += 1;
    };
  }, [loadServers]);

  const sortedServers = useMemo(() => {
    return servers.slice().sort((a, b) => a.name.localeCompare(b.name));
  }, [servers]);

  const resetCreateForm = () => {
    setName("");
    setBaseUrl("");
    setApiUsername("");
    setApiToken("");
    setVerifySsl(true);
    setCreateError(null);
    setCreating(false);
  };

  const openCreate = () => {
    resetCreateForm();
    setCreateOpen(true);
  };

  const createServer = async () => {
    setCreating(true);
    setCreateError(null);

    try {
      const response = await fetchWithAuth("/admin/whm/servers", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          name,
          base_url: baseUrl,
          api_username: apiUsername,
          api_token: apiToken,
          verify_ssl: verifySsl,
        }),
      });

      const payload = await jsonOrThrow<CreateWhmServerResponse>(response);
      setServers((prev) => [...prev, payload.server]);
      setCreateOpen(false);
      resetCreateForm();
    } catch (error) {
      setCreateError(toUserMessage(error, "Unable to create WHM server"));
    } finally {
      setCreating(false);
      setApiToken("");
    }
  };

  const validateServer = async (serverId: string) => {
    if (!serverId) return;
    setValidateBusyId(serverId);

    try {
      const response = await fetchWithAuth(`/admin/whm/servers/${serverId}/validate`, {
        method: "POST",
      });
      const payload = await jsonOrThrow<ValidateWhmServerResponse>(response);
      setValidateResultById((prev) => ({ ...prev, [serverId]: payload }));
    } catch (error) {
      setValidateResultById((prev) => ({
        ...prev,
        [serverId]: {
          ok: false,
          message: toUserMessage(error, "Validation failed"),
        },
      }));
    } finally {
      setValidateBusyId((prev) => (prev === serverId ? null : prev));
    }
  };

  const deleteServer = async (serverId: string) => {
    if (!serverId) return;
    setDeleteBusyId(serverId);
    setActionError(null);

    try {
      const response = await fetchWithAuth(`/admin/whm/servers/${serverId}`, {
        method: "DELETE",
      });
      await jsonOrThrow(response);
      setServers((prev) => prev.filter((server) => server.id !== serverId));
      setValidateResultById((prev) => {
        const next = { ...prev };
        delete next[serverId];
        return next;
      });
    } catch (error) {
      setActionError(toUserMessage(error, "Unable to delete WHM server"));
    } finally {
      setDeleteBusyId((prev) => (prev === serverId ? null : prev));
    }
  };

  const labelClass = "block text-sm font-medium text-text";
  const inputClass =
    "mt-1 w-full rounded-xl border border-border bg-surface/80 px-3 py-2.5 text-sm text-text shadow-sm outline-none placeholder:text-muted focus-visible:border-accent/60 focus-visible:ring-2 focus-visible:ring-accent/25 focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:cursor-not-allowed disabled:opacity-70";

  return (
    <Dialog.Root open={createOpen} onOpenChange={setCreateOpen}>
      <main className="min-h-dvh bg-bg p-6">
        <div className="flex items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold">WHM Servers</h1>
            <p className="mt-1 font-ui text-sm text-muted">
              Store WHM credentials in NOA (token is never shown after save).
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Button disabled={loading} onClick={() => void loadServers()} size="sm">
              Refresh
            </Button>
            <Dialog.Trigger asChild>
              <Button onClick={openCreate} variant="primary" size="sm">
                Add server
              </Button>
            </Dialog.Trigger>
          </div>
        </div>

        {loadError ? (
          <div
            role="alert"
            aria-live="assertive"
            className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 font-ui text-sm text-red-800"
          >
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">{loadError}</div>
              <Button className="shrink-0" onClick={() => void loadServers()} size="sm">
                Retry
              </Button>
            </div>
          </div>
        ) : null}

        {actionError ? (
          <div
            role="alert"
            aria-live="assertive"
            className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 font-ui text-sm text-red-800"
          >
            {actionError}
          </div>
        ) : null}

        <div className="panel mt-6 overflow-hidden">
          <table className="w-full font-ui text-sm">
            <thead className="bg-surface-2 text-muted">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Name</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Base URL</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">API user</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">SSL</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Updated</th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {loading ? (
                <tr>
                  <td colSpan={6} className="px-4 py-6 text-center text-muted">
                    Loading...
                  </td>
                </tr>
              ) : sortedServers.length ? (
                sortedServers.map((server) => {
                  const validateResult = validateResultById[server.id];
                  const busy = validateBusyId === server.id;
                  const deleting = deleteBusyId === server.id;
                  const badge =
                    validateResult === undefined
                      ? null
                      : validateResult.ok
                        ? { label: "validated", className: "status-badge-success" }
                        : { label: "failed", className: "status-badge-danger" };

                  return (
                    <tr key={server.id}>
                      <td className="px-4 py-3 text-text">{server.name}</td>
                      <td className="px-4 py-3 text-muted">{server.base_url}</td>
                      <td className="px-4 py-3 text-muted">{server.api_username}</td>
                      <td className="px-4 py-3 text-muted">{server.verify_ssl ? "on" : "off"}</td>
                      <td className="px-4 py-3 text-muted">{formatTimestamp(server.updated_at)}</td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <Button
                            disabled={busy || deleting}
                            onClick={() => void validateServer(server.id)}
                            size="sm"
                            variant="secondary"
                          >
                            {busy ? "Validating..." : "Validate"}
                          </Button>

                          <Button
                            disabled={deleting || busy}
                            onClick={() => void deleteServer(server.id)}
                            size="sm"
                            variant="danger"
                          >
                            {deleting ? "Deleting..." : "Delete"}
                          </Button>

                          {badge ? (
                            <span
                              className={["status-badge", badge.className].join(" ")}
                              title={validateResult?.message}
                            >
                              {badge.label}
                            </span>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={6} className="px-4 py-6 text-center text-muted">
                    No WHM servers configured.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30 opacity-0 transition-opacity data-[state=open]:opacity-100 data-[state=closed]:opacity-0" />

          <Dialog.Content className="fixed top-1/2 left-1/2 z-50 w-[min(92vw,520px)] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-2xl border border-border bg-bg shadow-[0_1.25rem_3rem_rgba(0,0,0,0.22)] outline-none">
            <div className="flex items-start justify-between gap-3 border-b border-border bg-surface/50 px-5 py-4">
              <div className="min-w-0">
                <Dialog.Title className="text-lg font-semibold text-text">Add WHM server</Dialog.Title>
                <Dialog.Description className="mt-1 font-ui text-sm text-muted">
                  Token is stored securely and never displayed.
                </Dialog.Description>
              </div>
              <Dialog.Close asChild>
                <Button aria-label="Close" className="text-muted hover:text-text" size="icon">
                  <Cross2Icon width={18} height={18} />
                </Button>
              </Dialog.Close>
            </div>

            <div className="px-5 py-4 font-ui">
              <div className="grid gap-4">
                <div>
                  <label className={labelClass} htmlFor="whm-name">
                    Name
                  </label>
                  <input
                    id="whm-name"
                    className={inputClass}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="web1"
                    required
                    disabled={creating}
                  />
                </div>

                <div>
                  <label className={labelClass} htmlFor="whm-base-url">
                    Base URL
                  </label>
                  <input
                    id="whm-base-url"
                    className={inputClass}
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder="https://whm.example.com:2087"
                    required
                    disabled={creating}
                  />
                </div>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <label className={labelClass} htmlFor="whm-api-username">
                      API username
                    </label>
                    <input
                      id="whm-api-username"
                      className={inputClass}
                      value={apiUsername}
                      onChange={(e) => setApiUsername(e.target.value)}
                      placeholder="root"
                      required
                      disabled={creating}
                    />
                  </div>
                  <div className="flex items-center gap-2 pt-7">
                    <input
                      id="whm-verify-ssl"
                      type="checkbox"
                      checked={verifySsl}
                      onChange={(e) => setVerifySsl(e.target.checked)}
                      disabled={creating}
                    />
                    <label htmlFor="whm-verify-ssl" className="text-sm text-text">
                      Verify SSL
                    </label>
                  </div>
                </div>

                <div>
                  <label className={labelClass} htmlFor="whm-api-token">
                    API token
                  </label>
                  <input
                    id="whm-api-token"
                    type="password"
                    className={inputClass}
                    value={apiToken}
                    onChange={(e) => setApiToken(e.target.value)}
                    placeholder="••••••••••"
                    required
                    disabled={creating}
                  />
                </div>
              </div>

              {createError ? (
                <p
                  role="alert"
                  aria-live="assertive"
                  className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
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
                <Button disabled={creating} onClick={() => void createServer()} size="sm" variant="primary">
                  {creating ? "Saving..." : "Save"}
                </Button>
              </div>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </main>
    </Dialog.Root>
  );
}
