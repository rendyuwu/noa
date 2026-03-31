"use client";

import type { Dispatch, SetStateAction } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import * as Dialog from "@radix-ui/react-dialog";
import { Cross2Icon } from "@radix-ui/react-icons";

import { Button } from "@/components/lib/button";
import { ConfirmAction } from "@/components/lib/confirm-dialog";
import { toUserMessage } from "@/components/lib/error-message";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";
import { ScrollArea } from "@/components/lib/scroll-area";

type ProxmoxServer = {
  id: string;
  name: string;
  base_url: string;
  api_token_id: string;
  has_api_token_secret: boolean;
  verify_ssl: boolean;
  created_at?: string;
  updated_at?: string;
};

type ListProxmoxServersResponse = {
  servers: ProxmoxServer[];
};

type CreateProxmoxServerResponse = {
  server: ProxmoxServer;
};

type UpdateProxmoxServerResponse = {
  server: ProxmoxServer;
};

type ValidateProxmoxServerResponse = {
  ok: boolean;
  error_code?: string | null;
  message: string;
};

type ProxmoxServerFormState = {
  name: string;
  baseUrl: string;
  apiTokenId: string;
  apiTokenSecret: string;
  verifySsl: boolean;
};

const labelClass = "block text-sm font-medium text-text";
const inputClass =
  "mt-1 w-full rounded-xl border border-border bg-surface/80 px-3 py-2.5 text-sm text-text shadow-sm outline-none placeholder:text-muted focus-visible:border-accent/60 focus-visible:ring-2 focus-visible:ring-accent/25 focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:cursor-not-allowed disabled:opacity-70";
const helperClass = "mt-1 font-ui text-xs text-muted";

const EMPTY_FORM_STATE: ProxmoxServerFormState = {
  name: "",
  baseUrl: "",
  apiTokenId: "",
  apiTokenSecret: "",
  verifySsl: false,
};

function formatTimestamp(value: unknown): string {
  if (typeof value !== "string" || !value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "Z");
}

function formStateFromServer(server: ProxmoxServer): ProxmoxServerFormState {
  return {
    name: server.name,
    baseUrl: server.base_url,
    apiTokenId: server.api_token_id,
    apiTokenSecret: "",
    verifySsl: server.verify_ssl,
  };
}

function validateForm(
  form: ProxmoxServerFormState,
  mode: "create" | "update",
): string | null {
  if (!form.name.trim()) return "Name is required";
  if (!form.baseUrl.trim()) return "Base URL is required";
  if (!form.apiTokenId.trim()) return "API token ID is required";
  if (mode === "create" && !form.apiTokenSecret.trim()) {
    return "API token secret is required";
  }
  return null;
}

function buildCreatePayload(form: ProxmoxServerFormState): Record<string, unknown> {
  return {
    name: form.name.trim(),
    base_url: form.baseUrl.trim(),
    api_token_id: form.apiTokenId.trim(),
    api_token_secret: form.apiTokenSecret.trim(),
    verify_ssl: form.verifySsl,
  };
}

function buildUpdatePayload(form: ProxmoxServerFormState): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    base_url: form.baseUrl.trim(),
    api_token_id: form.apiTokenId.trim(),
    verify_ssl: form.verifySsl,
  };

  if (form.apiTokenSecret.trim()) {
    payload.api_token_secret = form.apiTokenSecret.trim();
  }

  return payload;
}

type FormFieldsProps = {
  form: ProxmoxServerFormState;
  setForm: Dispatch<SetStateAction<ProxmoxServerFormState>>;
  disabled: boolean;
  mode: "create" | "update";
};

function ProxmoxServerFormFields({ form, setForm, disabled, mode }: FormFieldsProps) {
  const updateForm = <K extends keyof ProxmoxServerFormState>(
    key: K,
    value: ProxmoxServerFormState[K],
  ) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div className="grid gap-4">
      <div className="rounded-xl border border-border bg-surface/50 px-4 py-4">
        <div className="text-xs font-semibold uppercase tracking-wide text-muted">Proxmox API</div>
        <div className="mt-3 grid gap-4">
          <div>
            <label className={labelClass} htmlFor={`${mode}-proxmox-name`}>
              Name
            </label>
            <input
              id={`${mode}-proxmox-name`}
              className={inputClass}
              value={form.name}
              onChange={(event) => updateForm("name", event.target.value)}
              placeholder="pve1"
              required
              disabled={disabled}
            />
          </div>

          <div>
            <label className={labelClass} htmlFor={`${mode}-proxmox-base-url`}>
              Base URL
            </label>
            <input
              id={`${mode}-proxmox-base-url`}
              className={inputClass}
              value={form.baseUrl}
              onChange={(event) => updateForm("baseUrl", event.target.value)}
              placeholder="https://pve.example.com:8006"
              required
              disabled={disabled}
            />
          </div>

          <div>
            <label className={labelClass} htmlFor={`${mode}-proxmox-api-token-id`}>
              API token ID
            </label>
            <input
              id={`${mode}-proxmox-api-token-id`}
              className={inputClass}
              value={form.apiTokenId}
              onChange={(event) => updateForm("apiTokenId", event.target.value)}
              placeholder="user@pam!tokenname"
              required
              disabled={disabled}
            />
          </div>

          <div>
            <label className={labelClass} htmlFor={`${mode}-proxmox-api-token-secret`}>
              API token secret
            </label>
            <input
              id={`${mode}-proxmox-api-token-secret`}
              type="password"
              className={inputClass}
              value={form.apiTokenSecret}
              onChange={(event) => updateForm("apiTokenSecret", event.target.value)}
              placeholder={
                mode === "create"
                  ? "••••••••••"
                  : "Stored — enter a new secret to replace"
              }
              required={mode === "create"}
              disabled={disabled}
            />
            {mode === "update" ? (
              <p className={helperClass}>
                Leave blank to keep the stored API token secret.
              </p>
            ) : null}
          </div>

          <div className="flex items-center gap-2">
            <input
              id={`${mode}-proxmox-verify-ssl`}
              type="checkbox"
              checked={form.verifySsl}
              onChange={(event) => updateForm("verifySsl", event.target.checked)}
              disabled={disabled}
            />
            <label htmlFor={`${mode}-proxmox-verify-ssl`} className="text-sm text-text">
              Verify SSL
            </label>
          </div>
        </div>
      </div>
    </div>
  );
}

export function ProxmoxServersAdminPage() {
  const [servers, setServers] = useState<ProxmoxServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState<string | null>(null);

  const panelSeqRef = useRef(0);
  const openerRef = useRef<HTMLElement | null>(null);
  const selectedServerIdRef = useRef<string | null>(null);

  const [selectedServerId, setSelectedServerId] = useState<string | null>(null);
  const selectedServer = useMemo(() => {
    if (!selectedServerId) return null;
    return servers.find((server) => server.id === selectedServerId) ?? null;
  }, [selectedServerId, servers]);

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<ProxmoxServerFormState>(EMPTY_FORM_STATE);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [editOpen, setEditOpen] = useState(false);
  const [editForm, setEditForm] = useState<ProxmoxServerFormState>(EMPTY_FORM_STATE);
  const [savingEdit, setSavingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [validateBusyId, setValidateBusyId] = useState<string | null>(null);
  const [validateResultById, setValidateResultById] = useState<
    Record<string, ValidateProxmoxServerResponse>
  >({});
  const [deleteBusyId, setDeleteBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const loadSeqRef = useRef(0);

  useEffect(() => {
    selectedServerIdRef.current = selectedServerId;
  }, [selectedServerId]);

  const loadServers = useCallback(async () => {
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setLoadError(null);

    try {
      const response = await fetchWithAuth("/admin/proxmox/servers");
      const payload = await jsonOrThrow<ListProxmoxServersResponse>(response);
      if (seq !== loadSeqRef.current) return;
      setServers(Array.isArray(payload.servers) ? payload.servers : []);
    } catch (error) {
      if (seq !== loadSeqRef.current) return;
      setLoadError(toUserMessage(error, "Unable to load Proxmox servers"));
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
    setCreateForm(EMPTY_FORM_STATE);
    setCreateError(null);
    setCreating(false);
  };

  const closePanel = () => {
    panelSeqRef.current += 1;
    selectedServerIdRef.current = null;
    openerRef.current = null;
    setSelectedServerId(null);
    setEditOpen(false);
    setEditError(null);
  };

  const openPanelForServer = (server: ProxmoxServer, opener: HTMLElement | null) => {
    panelSeqRef.current += 1;
    openerRef.current = opener;
    selectedServerIdRef.current = server.id;
    setSelectedServerId(server.id);
    setActionError(null);
    setActionStatus(null);
    setEditOpen(false);
    setEditError(null);
    setEditForm(formStateFromServer(server));
  };

  const panelStillMatches = (seq: number, serverId: string) => {
    return panelSeqRef.current === seq && selectedServerIdRef.current === serverId;
  };

  const openCreate = () => {
    resetCreateForm();
    setActionStatus(null);
    setCreateOpen(true);
  };

  const openEdit = () => {
    if (!selectedServer) return;
    setEditForm(formStateFromServer(selectedServer));
    setEditError(null);
    setActionStatus(null);
    setEditOpen(true);
  };

  const createServer = async () => {
    const validationError = validateForm(createForm, "create");
    if (validationError) {
      setCreateError(validationError);
      return;
    }

    setCreating(true);
    setCreateError(null);
    setActionStatus(null);

    try {
      const response = await fetchWithAuth("/admin/proxmox/servers", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify(buildCreatePayload(createForm)),
      });

      const payload = await jsonOrThrow<CreateProxmoxServerResponse>(response);
      setServers((prev) => [...prev, payload.server]);
      setCreateOpen(false);
      resetCreateForm();
      setActionError(null);
      setActionStatus(`Saved ${payload.server.name}.`);
    } catch (error) {
      setCreateError(toUserMessage(error, "Unable to create Proxmox server"));
    } finally {
      setCreating(false);
      setCreateForm((prev) => ({ ...prev, apiTokenSecret: "" }));
    }
  };

  const updateServer = async () => {
    if (!selectedServer) return;

    const validationError = validateForm(editForm, "update");
    if (validationError) {
      setEditError(validationError);
      return;
    }

    setSavingEdit(true);
    setEditError(null);
    setActionStatus(null);

    try {
      const response = await fetchWithAuth(`/admin/proxmox/servers/${selectedServer.id}`, {
        method: "PATCH",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify(buildUpdatePayload(editForm)),
      });

      const payload = await jsonOrThrow<UpdateProxmoxServerResponse>(response);
      setServers((prev) =>
        prev.map((server) => (server.id === payload.server.id ? payload.server : server)),
      );
      setValidateResultById((prev) => {
        const next = { ...prev };
        delete next[selectedServer.id];
        return next;
      });
      setEditOpen(false);
      setEditForm(formStateFromServer(payload.server));
      setActionError(null);
      setActionStatus(`Saved changes for ${payload.server.name}.`);
    } catch (error) {
      setEditError(toUserMessage(error, "Unable to update Proxmox server"));
    } finally {
      setSavingEdit(false);
      setEditForm((prev) => ({ ...prev, apiTokenSecret: "" }));
    }
  };

  const validateServer = async (serverId: string) => {
    if (!serverId) return;
    setValidateBusyId(serverId);
    setActionError(null);
    setActionStatus(null);

    try {
      const response = await fetchWithAuth(`/admin/proxmox/servers/${serverId}/validate`, {
        method: "POST",
      });
      const payload = await jsonOrThrow<ValidateProxmoxServerResponse>(response);
      setValidateResultById((prev) => ({ ...prev, [serverId]: payload }));
      if (payload.ok) {
        setActionStatus(
          payload.message && payload.message !== "ok" ? payload.message : "Validation succeeded.",
        );
      } else {
        setActionError(payload.message || "Validation failed");
      }
      await loadServers();
    } catch (error) {
      const message = toUserMessage(error, "Validation failed");
      setValidateResultById((prev) => ({
        ...prev,
        [serverId]: {
          ok: false,
          message,
        },
      }));
      setActionError(message);
    } finally {
      setValidateBusyId((prev) => (prev === serverId ? null : prev));
    }
  };

  const deleteServer = async (serverId: string) => {
    if (!serverId) return;
    const seq = panelSeqRef.current;
    setDeleteBusyId(serverId);
    setActionError(null);
    setActionStatus(null);

    try {
      const response = await fetchWithAuth(`/admin/proxmox/servers/${serverId}`, {
        method: "DELETE",
      });
      await jsonOrThrow(response);
      setServers((prev) => prev.filter((server) => server.id !== serverId));
      setValidateResultById((prev) => {
        const next = { ...prev };
        delete next[serverId];
        return next;
      });
      setActionStatus("Proxmox server deleted.");
      if (panelStillMatches(seq, serverId)) {
        closePanel();
      }
    } catch (error) {
      if (panelStillMatches(seq, serverId)) {
        setActionError(toUserMessage(error, "Unable to delete Proxmox server"));
      }
    } finally {
      setDeleteBusyId((prev) => (prev === serverId ? null : prev));
    }
  };

  return (
    <>
      <Dialog.Root open={createOpen} onOpenChange={setCreateOpen}>
        <main className="min-h-dvh bg-bg p-6">
          <div className="flex items-end justify-between gap-3">
            <div>
              <h1 className="text-2xl font-semibold">Proxmox Servers</h1>
              <p className="mt-1 font-ui text-sm text-muted">
                Store Proxmox API credentials in NOA. Secrets are never shown after save.
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

          {actionStatus ? (
            <div
              role="status"
              aria-live="polite"
              className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 font-ui text-sm text-emerald-800"
            >
              {actionStatus}
            </div>
          ) : null}

          <div className="panel mt-6 overflow-hidden">
            <table className="w-full font-ui text-sm">
              <thead className="bg-surface-2 text-muted">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">
                    Name
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">
                    Base URL
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">
                    API token ID
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">
                    SSL
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">
                    Updated
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">
                    Manage
                  </th>
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
                    const badge =
                      validateResult === undefined
                        ? null
                        : validateResult.ok
                          ? { label: "Validated", className: "status-badge-success" }
                          : { label: "Failed", className: "status-badge-danger" };

                    return (
                      <tr
                        key={server.id}
                        tabIndex={0}
                        aria-haspopup="dialog"
                        aria-label={`Manage ${server.name}`}
                        className="cursor-pointer transition-colors hover:bg-surface-2/60 focus-visible:bg-surface-2/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent/40"
                        onClick={(event) => openPanelForServer(server, event.currentTarget)}
                        onKeyDown={(event) => {
                          if (
                            event.key === "Enter" ||
                            event.key === " " ||
                            event.key === "Spacebar"
                          ) {
                            event.preventDefault();
                            openPanelForServer(server, event.currentTarget);
                          }
                        }}
                      >
                        <td className="px-4 py-3 text-text">
                          <div className="font-medium text-text">{server.name}</div>
                          <div className="mt-1 flex flex-wrap items-center gap-2">
                            {badge ? (
                              <span className={["status-badge", badge.className].join(" ")}>
                                {badge.label}
                              </span>
                            ) : null}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-muted">{server.base_url}</td>
                        <td className="px-4 py-3 text-muted">{server.api_token_id}</td>
                        <td className="px-4 py-3 text-muted">{server.verify_ssl ? "on" : "off"}</td>
                        <td className="px-4 py-3 text-muted">{formatTimestamp(server.updated_at)}</td>
                        <td className="px-4 py-3 text-muted">Manage</td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={6} className="px-4 py-6 text-center text-muted">
                      No Proxmox servers configured.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30 opacity-0 transition-opacity data-[state=open]:opacity-100 data-[state=closed]:opacity-0" />

            <Dialog.Content className="fixed top-1/2 left-1/2 z-50 flex max-h-[90vh] w-[min(92vw,760px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-bg shadow-[0_1.25rem_3rem_rgba(0,0,0,0.22)] outline-none">
              <div className="flex items-start justify-between gap-3 border-b border-border bg-surface/50 px-5 py-4">
                <div className="min-w-0">
                  <Dialog.Title className="text-lg font-semibold text-text">
                    Add Proxmox server
                  </Dialog.Title>
                  <Dialog.Description className="mt-1 font-ui text-sm text-muted">
                    Proxmox API token secret is stored securely and never displayed again.
                  </Dialog.Description>
                </div>
                <Dialog.Close asChild>
                  <Button aria-label="Close" className="text-muted hover:text-text" size="icon">
                    <Cross2Icon width={18} height={18} />
                  </Button>
                </Dialog.Close>
              </div>

              <ScrollArea
                className="min-h-0 flex-1"
                viewportClassName="h-full px-5 py-4 font-ui"
              >
                <ProxmoxServerFormFields
                  form={createForm}
                  setForm={setCreateForm}
                  disabled={creating}
                  mode="create"
                />

                {createError ? (
                  <p
                    role="alert"
                    aria-live="assertive"
                    className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
                  >
                    {createError}
                  </p>
                ) : null}
              </ScrollArea>

              <div className="mt-auto flex items-center justify-end gap-2 border-t border-border px-5 py-4">
                <Dialog.Close asChild>
                  <Button disabled={creating} size="sm">
                    Cancel
                  </Button>
                </Dialog.Close>
                <Button
                  disabled={creating}
                  onClick={() => void createServer()}
                  size="sm"
                  variant="primary"
                >
                  {creating ? "Saving..." : "Save"}
                </Button>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </main>
      </Dialog.Root>

      <Dialog.Root open={editOpen} onOpenChange={setEditOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30 opacity-0 transition-opacity data-[state=open]:opacity-100 data-[state=closed]:opacity-0" />
          <Dialog.Content className="fixed top-1/2 left-1/2 z-[60] flex max-h-[90vh] w-[min(92vw,760px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-bg shadow-[0_1.25rem_3rem_rgba(0,0,0,0.22)] outline-none">
            <div className="flex items-start justify-between gap-3 border-b border-border bg-surface/50 px-5 py-4">
              <div className="min-w-0">
                <Dialog.Title className="text-lg font-semibold text-text">
                  Edit Proxmox server
                </Dialog.Title>
                <Dialog.Description className="mt-1 font-ui text-sm text-muted">
                  Stored secrets can be replaced, but they are never shown again.
                </Dialog.Description>
              </div>
              <Dialog.Close asChild>
                <Button aria-label="Close" className="text-muted hover:text-text" size="icon">
                  <Cross2Icon width={18} height={18} />
                </Button>
              </Dialog.Close>
            </div>

            <ScrollArea
              className="min-h-0 flex-1"
              viewportClassName="h-full px-5 py-4 font-ui"
            >
              <ProxmoxServerFormFields
                form={editForm}
                setForm={setEditForm}
                disabled={savingEdit}
                mode="update"
              />

              {editError ? (
                <p
                  role="alert"
                  aria-live="assertive"
                  className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
                >
                  {editError}
                </p>
              ) : null}
            </ScrollArea>

            <div className="mt-auto flex items-center justify-end gap-2 border-t border-border px-5 py-4">
              <Dialog.Close asChild>
                <Button disabled={savingEdit} size="sm">
                  Cancel
                </Button>
              </Dialog.Close>
              <Button
                disabled={savingEdit}
                onClick={() => void updateServer()}
                size="sm"
                variant="primary"
              >
                {savingEdit ? "Saving..." : "Save changes"}
              </Button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root
        open={selectedServerId !== null}
        onOpenChange={(open) => {
          if (!open) closePanel();
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30 opacity-0 transition-opacity data-[state=open]:opacity-100 data-[state=closed]:opacity-0" />
          <Dialog.Content
            className={[
              "fixed inset-y-0 right-0 z-50 w-[30rem] max-w-[92vw]",
              "border-l border-border bg-bg shadow-md",
              "transition-transform duration-200 ease-out",
              "data-[state=open]:translate-x-0 data-[state=closed]:translate-x-full",
              "outline-none",
            ].join(" ")}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              openerRef.current?.focus();
            }}
          >
            <Dialog.Title className="sr-only">Manage Proxmox server</Dialog.Title>
            <Dialog.Description className="sr-only">
              Review server details, edit stored credentials, validate the connection, or delete the
              server.
            </Dialog.Description>

            <div className="flex h-full flex-col">
              <div className="flex items-start justify-between gap-3 border-b border-border bg-surface px-4 py-4">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-text">
                    {selectedServer?.name ?? "Proxmox server"}
                  </div>
                  {selectedServer?.base_url ? (
                    <div className="mt-0.5 text-xs text-muted">{selectedServer.base_url}</div>
                  ) : null}
                </div>
                <Dialog.Close asChild>
                  <Button aria-label="Close" size="icon">
                    <Cross2Icon width={16} height={16} />
                  </Button>
                </Dialog.Close>
              </div>

              <ScrollArea
                className="flex-1 min-h-0 font-ui"
                horizontalScrollbar
                viewportClassName="h-full p-4"
              >
                <div className="rounded-xl border border-border bg-surface px-4 py-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-wide text-muted">
                        Server details
                      </div>
                    </div>
                    <Button
                      disabled={!selectedServer || deleteBusyId === selectedServer.id}
                      onClick={openEdit}
                      size="sm"
                    >
                      Edit server
                    </Button>
                  </div>
                  <dl className="mt-3 grid gap-3 text-sm">
                    <div>
                      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">
                        Name
                      </dt>
                      <dd className="mt-1 text-text">{selectedServer?.name ?? "-"}</dd>
                    </div>
                    <div>
                      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">
                        Base URL
                      </dt>
                      <dd className="mt-1 break-all text-text">{selectedServer?.base_url ?? "-"}</dd>
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <div>
                        <dt className="text-xs font-semibold uppercase tracking-wide text-muted">
                          API token ID
                        </dt>
                        <dd className="mt-1 text-text">{selectedServer?.api_token_id ?? "-"}</dd>
                      </div>
                      <div>
                        <dt className="text-xs font-semibold uppercase tracking-wide text-muted">
                          SSL
                        </dt>
                        <dd className="mt-1 text-text">
                          {selectedServer?.verify_ssl ? "Verify enabled" : "Verification off"}
                        </dd>
                      </div>
                    </div>
                    <div>
                      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">
                        API secret
                      </dt>
                      <dd className="mt-1 flex items-center gap-2">
                        {selectedServer?.has_api_token_secret ? (
                          <span className="status-badge status-badge-success">Stored</span>
                        ) : (
                          <span className="status-badge">Not configured</span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">
                        Updated
                      </dt>
                      <dd className="mt-1 text-text">{formatTimestamp(selectedServer?.updated_at)}</dd>
                    </div>
                  </dl>
                </div>

                <div className="mt-4 rounded-xl border border-border bg-surface px-4 py-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold uppercase tracking-wide text-muted">
                        Latest validation
                      </div>
                      <div className="mt-2 flex items-center gap-2">
                        {selectedServer && validateResultById[selectedServer.id] ? (
                          <span
                            className={[
                              "status-badge",
                              validateResultById[selectedServer.id]?.ok
                                ? "status-badge-success"
                                : "status-badge-danger",
                            ].join(" ")}
                          >
                            {validateResultById[selectedServer.id]?.ok ? "Validated" : "Failed"}
                          </span>
                        ) : (
                          <span className="status-badge">Not run</span>
                        )}
                      </div>
                      <p className="mt-2 text-sm text-muted">
                        {selectedServer && validateResultById[selectedServer.id]
                          ? validateResultById[selectedServer.id]?.message
                          : "Validate checks the Proxmox API connection."}
                      </p>
                    </div>
                    <Button
                      className="shrink-0"
                      disabled={
                        !selectedServer ||
                        validateBusyId === selectedServer.id ||
                        deleteBusyId === selectedServer.id
                      }
                      onClick={() => selectedServer && void validateServer(selectedServer.id)}
                      size="sm"
                      variant="primary"
                    >
                      {selectedServer && validateBusyId === selectedServer.id
                        ? "Validating..."
                        : "Validate"}
                    </Button>
                  </div>
                </div>

                {actionError ? (
                  <p
                    role="alert"
                    aria-live="assertive"
                    className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
                  >
                    {actionError}
                  </p>
                ) : null}

                <div className="danger-zone mt-6">
                  <div className="danger-zone-label text-xs font-semibold uppercase tracking-wide">
                    Danger zone
                  </div>
                  <p className="danger-zone-copy mt-1 text-sm">
                    Delete this server configuration from NOA. Stored validation state for this
                    session is removed too.
                  </p>
                  <ConfirmAction
                    title="Delete server?"
                    description={
                      selectedServer
                        ? `This permanently deletes the ${selectedServer.name} server configuration from NOA.`
                        : "This permanently deletes this server configuration from NOA."
                    }
                    confirmLabel="Delete server"
                    confirmBusyLabel="Deleting..."
                    confirmVariant="danger"
                    busy={Boolean(selectedServer && deleteBusyId === selectedServer.id)}
                    error={actionError}
                    onConfirm={() => selectedServer && void deleteServer(selectedServer.id)}
                    trigger={({ open, disabled }) => (
                      <Button
                        className="mt-3 w-full"
                        disabled={
                          !selectedServer ||
                          disabled ||
                          (selectedServer && validateBusyId === selectedServer.id)
                        }
                        onClick={() => {
                          setActionError(null);
                          open();
                        }}
                        variant="danger"
                      >
                        Delete server
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
