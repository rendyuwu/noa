"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchWithAuth, jsonOrThrow } from "@/components/lib/http/fetch-client";
import { toErrorMessage } from "@/components/lib/http/error-message";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";

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

const EMPTY_FORM_STATE: ProxmoxServerFormState = {
  name: "",
  baseUrl: "",
  apiTokenId: "",
  apiTokenSecret: "",
  verifySsl: false,
};

const inputClass =
  "mt-1 w-full rounded-xl border border-border bg-surface/80 px-3 py-2.5 text-sm text-text shadow-sm outline-none placeholder:text-muted";

function formatTimestamp(value: unknown): string {
  if (typeof value !== "string" || !value) {
    return "-";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "-";
  }

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

function validateForm(form: ProxmoxServerFormState, mode: "create" | "update"): string | null {
  if (!form.name.trim()) {
    return "Name is required";
  }

  if (!form.baseUrl.trim()) {
    return "Base URL is required";
  }

  if (!form.apiTokenId.trim()) {
    return "API token ID is required";
  }

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

function ProxmoxFormFields({
  form,
  setForm,
  disabled,
  mode,
}: {
  form: ProxmoxServerFormState;
  setForm: (updater: (current: ProxmoxServerFormState) => ProxmoxServerFormState) => void;
  disabled: boolean;
  mode: "create" | "update";
}) {
  const updateField = <K extends keyof ProxmoxServerFormState>(key: K, value: ProxmoxServerFormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <label className="text-sm text-text">
        Name
        <input
          className={inputClass}
          value={form.name}
          onChange={(event) => updateField("name", event.target.value)}
          disabled={disabled}
        />
      </label>
      <label className="text-sm text-text">
        Base URL
        <input
          className={inputClass}
          value={form.baseUrl}
          onChange={(event) => updateField("baseUrl", event.target.value)}
          disabled={disabled}
        />
      </label>
      <label className="text-sm text-text">
        API token ID
        <input
          className={inputClass}
          value={form.apiTokenId}
          onChange={(event) => updateField("apiTokenId", event.target.value)}
          disabled={disabled}
        />
      </label>
      <label className="text-sm text-text">
        API token secret
        <input
          type="password"
          className={inputClass}
          value={form.apiTokenSecret}
          onChange={(event) => updateField("apiTokenSecret", event.target.value)}
          placeholder={mode === "update" ? "Leave blank to keep current" : "Proxmox token secret"}
          disabled={disabled}
        />
      </label>
      <label className="inline-flex items-center gap-2 text-sm text-text md:col-span-2">
        <input
          type="checkbox"
          checked={form.verifySsl}
          onChange={(event) => updateField("verifySsl", event.target.checked)}
          disabled={disabled}
        />
        Verify SSL
      </label>
    </div>
  );
}

export function ProxmoxServersAdminPage() {
  const [servers, setServers] = useState<ProxmoxServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionStatus, setActionStatus] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [selectedServerId, setSelectedServerId] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [createForm, setCreateForm] = useState<ProxmoxServerFormState>(EMPTY_FORM_STATE);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [editOpen, setEditOpen] = useState(false);
  const [editForm, setEditForm] = useState<ProxmoxServerFormState>(EMPTY_FORM_STATE);
  const [savingEdit, setSavingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [validateBusyId, setValidateBusyId] = useState<string | null>(null);
  const [validateResultById, setValidateResultById] = useState<Record<string, ValidateProxmoxServerResponse>>({});

  const selectedServer = useMemo(() => {
    if (!selectedServerId) {
      return null;
    }

    return servers.find((server) => server.id === selectedServerId) ?? null;
  }, [selectedServerId, servers]);

  const loadServers = useCallback(async () => {
    setLoading(true);
    setLoadError(null);

    try {
      const response = await fetchWithAuth("/admin/proxmox/servers");
      const payload = await jsonOrThrow<ListProxmoxServersResponse>(response);
      const nextServers = Array.isArray(payload.servers) ? payload.servers : [];
      setServers(nextServers);
      setSelectedServerId((current) => {
        if (current && nextServers.some((server) => server.id === current)) {
          return current;
        }

        return nextServers[0]?.id ?? null;
      });
    } catch (error) {
      setLoadError(toErrorMessage(error, "Unable to load Proxmox servers"));
      setServers([]);
      setSelectedServerId(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadServers();
  }, [loadServers]);

  const sortedServers = useMemo(
    () => servers.slice().sort((a, b) => a.name.localeCompare(b.name)),
    [servers],
  );

  const openEdit = () => {
    if (!selectedServer) {
      return;
    }

    setEditForm(formStateFromServer(selectedServer));
    setEditError(null);
    setEditOpen(true);
  };

  const closeCreate = () => {
    setCreateOpen(false);
    setCreateError(null);
    setCreateForm(EMPTY_FORM_STATE);
  };

  const closeEdit = () => {
    setEditOpen(false);
    setEditError(null);
  };

  const createServer = async () => {
    const validationError = validateForm(createForm, "create");
    if (validationError) {
      setCreateError(validationError);
      return;
    }

    setCreating(true);
    setCreateError(null);

    try {
      const response = await fetchWithAuth("/admin/proxmox/servers", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(buildCreatePayload(createForm)),
      });
      const payload = await jsonOrThrow<CreateProxmoxServerResponse>(response);

      setServers((current) => [payload.server, ...current]);
      setSelectedServerId(payload.server.id);
      setActionStatus(`Created ${payload.server.name}.`);
      closeCreate();
    } catch (error) {
      setCreateError(toErrorMessage(error, "Unable to create Proxmox server"));
    } finally {
      setCreating(false);
    }
  };

  const saveEdit = async () => {
    if (!selectedServer) {
      return;
    }

    const validationError = validateForm(editForm, "update");
    if (validationError) {
      setEditError(validationError);
      return;
    }

    setSavingEdit(true);
    setEditError(null);

    try {
      const response = await fetchWithAuth(`/admin/proxmox/servers/${selectedServer.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(buildUpdatePayload(editForm)),
      });
      const payload = await jsonOrThrow<UpdateProxmoxServerResponse>(response);

      setServers((current) => current.map((server) => (server.id === payload.server.id ? payload.server : server)));
      setActionStatus(`Saved changes for ${payload.server.name}.`);
      closeEdit();
    } catch (error) {
      setEditError(toErrorMessage(error, "Unable to update Proxmox server"));
    } finally {
      setSavingEdit(false);
    }
  };

  const deleteServer = async () => {
    if (!selectedServer) {
      return;
    }

    setConfirmDeleteOpen(false);
    try {
      const response = await fetchWithAuth(`/admin/proxmox/servers/${selectedServer.id}`, { method: "DELETE" });
      await jsonOrThrow<{ ok: boolean }>(response);
      setServers((current) => current.filter((server) => server.id !== selectedServer.id));
      setActionStatus(`Deleted ${selectedServer.name}.`);
      setSelectedServerId(null);
    } catch (error) {
      setActionError(toErrorMessage(error, "Unable to delete Proxmox server"));
    }
  };

  const validateServer = async (serverId: string) => {
    setValidateBusyId(serverId);
    setActionError(null);

    try {
      const response = await fetchWithAuth(`/admin/proxmox/servers/${serverId}/validate`, { method: "POST" });
      const payload = await jsonOrThrow<ValidateProxmoxServerResponse>(response);
      setValidateResultById((current) => ({ ...current, [serverId]: payload }));
      await loadServers();
    } catch (error) {
      setActionError(toErrorMessage(error, "Unable to validate Proxmox server"));
    } finally {
      setValidateBusyId(null);
    }
  };

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold text-text">Proxmox servers</h2>
          <p className="text-sm text-muted">Manage Proxmox API connection profiles.</p>
        </div>
        <button
          type="button"
          className="rounded-xl bg-accent px-3 py-2 text-sm font-medium text-accent-foreground"
          onClick={() => setCreateOpen((value) => !value)}
        >
          {createOpen ? "Close" : "Add server"}
        </button>
      </div>

      {loadError ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900" role="alert">
          {loadError}
        </div>
      ) : null}
      {actionError ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-900" role="alert">
          {actionError}
        </div>
      ) : null}
      {actionStatus ? (
        <div className="rounded-xl border border-border bg-surface px-3 py-2 text-sm text-text" role="status">
          {actionStatus}
        </div>
      ) : null}

      {createOpen ? (
        <div className="rounded-2xl border border-border bg-surface p-4">
          <h3 className="text-base font-semibold text-text">Create server</h3>
          <div className="mt-3">
            <ProxmoxFormFields form={createForm} setForm={setCreateForm} disabled={creating} mode="create" />
          </div>
          {createError ? <p className="mt-3 text-sm text-red-700">{createError}</p> : null}
          <div className="mt-4 flex items-center gap-2">
            <button
              type="button"
              className="rounded-xl bg-accent px-3 py-2 text-sm font-medium text-accent-foreground"
              onClick={() => void createServer()}
              disabled={creating}
            >
              {creating ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              className="rounded-xl border border-border bg-bg px-3 py-2 text-sm font-medium text-text"
              onClick={closeCreate}
              disabled={creating}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      <div className="overflow-x-auto rounded-2xl border border-border bg-surface">
        <table className="min-w-full divide-y divide-border text-sm">
          <thead>
            <tr className="bg-bg/60 text-left text-xs uppercase tracking-[0.08em] text-muted">
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2">Base URL</th>
              <th className="px-3 py-2">Token ID</th>
              <th className="px-3 py-2">SSL</th>
              <th className="px-3 py-2">Updated</th>
              <th className="px-3 py-2">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {loading ? (
              <tr>
                <td className="px-3 py-3 text-muted" colSpan={6}>
                  Loading servers…
                </td>
              </tr>
            ) : sortedServers.length === 0 ? (
              <tr>
                <td className="px-3 py-3 text-muted" colSpan={6}>
                  No Proxmox servers configured.
                </td>
              </tr>
            ) : (
              sortedServers.map((server) => (
                <tr
                  key={server.id}
                  className={selectedServerId === server.id ? "bg-accent/5" : undefined}
                  onClick={() => setSelectedServerId(server.id)}
                >
                  <td className="px-3 py-3 font-medium text-text">{server.name}</td>
                  <td className="px-3 py-3 text-muted">{server.base_url}</td>
                  <td className="px-3 py-3 text-text">{server.api_token_id}</td>
                  <td className="px-3 py-3 text-text">{server.verify_ssl ? "on" : "off"}</td>
                  <td className="px-3 py-3 text-muted">{formatTimestamp(server.updated_at)}</td>
                  <td className="px-3 py-3">
                    <button
                      type="button"
                      className="text-accent underline underline-offset-2"
                      onClick={() => setSelectedServerId(server.id)}
                    >
                      Manage {server.name}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {selectedServer ? (
        <div className="rounded-2xl border border-border bg-surface p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-base font-semibold text-text">Server details</h3>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="rounded-xl border border-border bg-bg px-3 py-2 text-sm text-text"
                onClick={() => void validateServer(selectedServer.id)}
                disabled={validateBusyId === selectedServer.id}
              >
                {validateBusyId === selectedServer.id ? "Validating…" : "Validate"}
              </button>
              <button
                type="button"
                className="rounded-xl border border-border bg-bg px-3 py-2 text-sm text-text"
                onClick={openEdit}
              >
                Edit server
              </button>
              <AlertDialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
                <AlertDialogTrigger asChild>
                  <button
                    type="button"
                    className="rounded-xl border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800"
                  >
                    Delete server
                  </button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Delete server</AlertDialogTitle>
                    <AlertDialogDescription>
                      Delete {selectedServer.name}? This cannot be undone.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction onClick={() => void deleteServer()}>Delete</AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </div>

          {validateResultById[selectedServer.id] ? (
            <div
              className={`mt-3 rounded-xl border px-3 py-2 text-sm ${
                validateResultById[selectedServer.id]?.ok
                  ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                  : "border-amber-200 bg-amber-50 text-amber-900"
              }`}
            >
              {validateResultById[selectedServer.id]?.message}
            </div>
          ) : null}
        </div>
      ) : null}

      {editOpen && selectedServer ? (
        <div className="rounded-2xl border border-border bg-surface p-4">
          <h3 className="text-base font-semibold text-text">Edit server</h3>
          <div className="mt-3">
            <ProxmoxFormFields form={editForm} setForm={setEditForm} disabled={savingEdit} mode="update" />
          </div>
          {editError ? <p className="mt-3 text-sm text-red-700">{editError}</p> : null}
          <div className="mt-4 flex items-center gap-2">
            <button
              type="button"
              className="rounded-xl bg-accent px-3 py-2 text-sm font-medium text-accent-foreground"
              onClick={() => void saveEdit()}
              disabled={savingEdit}
            >
              {savingEdit ? "Saving…" : "Save changes"}
            </button>
            <button
              type="button"
              className="rounded-xl border border-border bg-bg px-3 py-2 text-sm font-medium text-text"
              onClick={closeEdit}
              disabled={savingEdit}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
