"use client";

import type { Dispatch, SetStateAction } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AdminDetailModal } from "@/components/admin/admin-detail-modal";
import { AdminListLayout } from "@/components/admin/admin-list-layout";
import { AdminStatusBadge } from "@/components/admin/admin-status-badge";
import { Button } from "@/components/ui/button";
import { ConfirmAction } from "@/components/lib/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toUserMessage } from "@/components/lib/error-message";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";

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

const labelClass = "block text-sm font-medium text-foreground";
const inputClass =
  "mt-1 w-full rounded-xl border border-border bg-card/80 px-3 py-2.5 text-sm text-foreground shadow-sm outline-none placeholder:text-muted-foreground focus-visible:border-primary/60 focus-visible:ring-2 focus-visible:ring-primary/25 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-70";
const helperClass = "mt-1 font-sans text-xs text-muted-foreground";

const EMPTY_FORM_STATE: ProxmoxServerFormState = {
  name: "",
  baseUrl: "",
  apiTokenId: "",
  apiTokenSecret: "",
  verifySsl: false,
};

function formatRelativeTime(value: unknown): string {
  if (typeof value !== "string" || !value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  const now = Date.now();
  const diffMs = now - date.getTime();
  if (diffMs < 0) return "Just now";
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return "Just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
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

function getValidationSummary(result: ValidateProxmoxServerResponse | undefined): {
  label: string;
  tone: "muted" | "success" | "danger";
} {
  if (!result) return { label: "Not run", tone: "muted" };
  return result.ok
    ? { label: "Validated", tone: "success" }
    : { label: "Failed", tone: "danger" };
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
      <div className="rounded-xl border border-border bg-card/50 px-4 py-4">
        <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Proxmox API</div>
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
            <label htmlFor={`${mode}-proxmox-verify-ssl`} className="text-sm text-foreground">
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
    setSelectedServerId(null);
    setEditOpen(false);
    setEditError(null);
  };

  const openPanelForServer = (server: ProxmoxServer) => {
    panelSeqRef.current += 1;
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
      <AdminListLayout
        title="Proxmox Servers"
        description="Store Proxmox API credentials in NOA. Secrets are never shown after save."
        loading={loading}
        error={loadError}
        onRetry={() => void loadServers()}
        empty={!loading && !loadError && servers.length === 0}
        emptyTitle="No Proxmox servers"
        emptyDescription="Add a Proxmox server to manage virtualization infrastructure."
        actions={
          <>
            <Button disabled={loading} onClick={() => void loadServers()} size="sm" variant="outline">
              Refresh
            </Button>
            <Button onClick={openCreate} variant="default" size="sm">
              Add server
            </Button>
          </>
        }
      >
        {actionStatus ? (
          <div
            role="status"
            aria-live="polite"
            className="rounded-xl border border-success/25 bg-success/10 px-3 py-2 font-sans text-sm text-success mb-2"
          >
            {actionStatus}
          </div>
        ) : null}

        {actionError ? (
          <div
            role="alert"
            aria-live="assertive"
            className="rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 font-sans text-sm text-destructive mb-2"
          >
            {actionError}
          </div>
        ) : null}

        <div className="panel overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full border-collapse text-left">
              <thead className="bg-muted/50">
                <tr>
                  <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Server
                  </th>
                  <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Validation
                  </th>
                  <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    SSL
                  </th>
                  <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Token secret
                  </th>
                  <th scope="col" className="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Updated
                  </th>
                  <th scope="col" className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {sortedServers.map((server) => {
                  const validation = getValidationSummary(validateResultById[server.id]);

                  return (
                    <tr
                      key={server.id}
                      aria-selected={selectedServerId === server.id}
                      className={selectedServerId === server.id ? "bg-accent/40" : "bg-card"}
                    >
                      <th scope="row" className="px-4 py-3 align-top font-normal">
                        <div className="text-sm font-medium text-foreground">{server.name}</div>
                        <div className="mt-1 text-sm text-muted-foreground">{server.base_url}</div>
                      </th>
                      <td className="px-4 py-3 align-top">
                        <AdminStatusBadge tone={validation.tone}>{validation.label}</AdminStatusBadge>
                      </td>
                      <td className="px-4 py-3 align-top text-sm text-foreground">
                        {server.verify_ssl ? "Verify enabled" : "Verification off"}
                      </td>
                      <td className="px-4 py-3 align-top">
                        <AdminStatusBadge tone={server.has_api_token_secret ? "success" : "muted"}>
                          {server.has_api_token_secret ? "Stored" : "Not configured"}
                        </AdminStatusBadge>
                      </td>
                      <td className="px-4 py-3 align-top text-sm text-muted-foreground">
                        {formatRelativeTime(server.updated_at)}
                      </td>
                      <td className="px-4 py-3 align-top text-right">
                        <Button onClick={() => openPanelForServer(server)} size="sm" variant="outline">
                          {`Manage ${server.name}`}
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </AdminListLayout>

      {/* Detail modal */}
      <AdminDetailModal
        open={selectedServerId !== null}
        onOpenChange={(open) => { if (!open) closePanel(); }}
        title={selectedServer?.name ?? "Proxmox server"}
        subtitle={selectedServer?.base_url}
        size="lg"
        headerActions={
          <Button
            disabled={!selectedServer || deleteBusyId === selectedServer.id}
            onClick={openEdit}
            size="sm"
            variant="outline"
          >
            Edit server
          </Button>
        }
      >
        <div className="panel px-4 py-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Server details
          </div>
          <dl className="mt-3 grid gap-3 text-sm">
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Name</dt>
              <dd className="mt-1 text-foreground">{selectedServer?.name ?? "-"}</dd>
            </div>
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Base URL</dt>
              <dd className="mt-1 break-all text-foreground">{selectedServer?.base_url ?? "-"}</dd>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">API token ID</dt>
                <dd className="mt-1 text-foreground">{selectedServer?.api_token_id ?? "-"}</dd>
              </div>
              <div>
                <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">SSL</dt>
                <dd className="mt-1 text-foreground">
                  {selectedServer?.verify_ssl ? "Verify enabled" : "Verification off"}
                </dd>
              </div>
            </div>
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">API secret</dt>
              <dd className="mt-1 flex items-center gap-2">
                <AdminStatusBadge tone={selectedServer?.has_api_token_secret ? "success" : "muted"}>
                  {selectedServer?.has_api_token_secret ? "Stored" : "Not configured"}
                </AdminStatusBadge>
              </dd>
            </div>
            <div>
              <dt className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Updated</dt>
              <dd className="mt-1 text-foreground">{formatRelativeTime(selectedServer?.updated_at)}</dd>
            </div>
          </dl>
        </div>

        {/* Validation card */}
        <div className="panel mt-4 px-4 py-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Latest validation
              </div>
              <div className="mt-2 flex items-center gap-2">
                {selectedServer ? (
                  <AdminStatusBadge tone={getValidationSummary(validateResultById[selectedServer.id]).tone}>
                    {getValidationSummary(validateResultById[selectedServer.id]).label}
                  </AdminStatusBadge>
                ) : null}
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
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
              variant="default"
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
            className="mt-4 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {actionError}
          </p>
        ) : null}

        {/* Danger zone */}
        <div className="danger-zone mt-6">
          <div className="danger-zone-label text-xs font-semibold uppercase tracking-wide">Danger zone</div>
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
                variant="destructive"
              >
                Delete server
              </Button>
            )}
          />
        </div>
      </AdminDetailModal>

      {/* Create server dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-3xl max-h-[90vh] flex flex-col gap-0 p-0">
          <DialogHeader className="px-6 pt-6 pb-4 border-b border-border shrink-0">
            <DialogTitle>Add Proxmox server</DialogTitle>
            <DialogDescription>
              Proxmox API token secret is stored securely and never displayed again.
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 min-h-0 overflow-y-auto px-6 py-4 font-sans">
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
                className="mt-4 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {createError}
              </p>
            ) : null}
          </div>

          <div className="px-6 py-4 border-t border-border shrink-0 flex justify-end gap-2">
            <Button disabled={creating} onClick={() => setCreateOpen(false)} size="sm" variant="outline">
              Cancel
            </Button>
            <Button disabled={creating} onClick={() => void createServer()} size="sm" variant="default">
              {creating ? "Saving..." : "Save"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Edit server dialog */}
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent className="max-w-3xl max-h-[90vh] flex flex-col gap-0 p-0">
          <DialogHeader className="px-6 pt-6 pb-4 border-b border-border shrink-0">
            <DialogTitle>Edit Proxmox server</DialogTitle>
            <DialogDescription>
              Stored secrets can be replaced, but they are never shown again.
            </DialogDescription>
          </DialogHeader>

          <div className="flex-1 min-h-0 overflow-y-auto px-6 py-4 font-sans">
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
                className="mt-4 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {editError}
              </p>
            ) : null}
          </div>

          <div className="px-6 py-4 border-t border-border shrink-0 flex justify-end gap-2">
            <Button disabled={savingEdit} onClick={() => setEditOpen(false)} size="sm" variant="outline">
              Cancel
            </Button>
            <Button disabled={savingEdit} onClick={() => void updateServer()} size="sm" variant="default">
              {savingEdit ? "Saving..." : "Save changes"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
