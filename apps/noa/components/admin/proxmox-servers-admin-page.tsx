"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { fetchWithAuth, jsonOrThrow } from "@/components/lib/http/fetch-client";
import { toErrorMessage } from "@/components/lib/http/error-message";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
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
      <label className="text-sm text-text" htmlFor="proxmox-server-name">
        Name
        <Input
          id="proxmox-server-name"
          className="mt-1"
          value={form.name}
          onChange={(event) => updateField("name", event.target.value)}
          disabled={disabled}
        />
      </label>
      <label className="text-sm text-text" htmlFor="proxmox-server-base-url">
        Base URL
        <Input
          id="proxmox-server-base-url"
          className="mt-1"
          value={form.baseUrl}
          onChange={(event) => updateField("baseUrl", event.target.value)}
          disabled={disabled}
        />
      </label>
      <label className="text-sm text-text" htmlFor="proxmox-server-api-token-id">
        API token ID
        <Input
          id="proxmox-server-api-token-id"
          className="mt-1"
          value={form.apiTokenId}
          onChange={(event) => updateField("apiTokenId", event.target.value)}
          disabled={disabled}
        />
      </label>
      <label className="text-sm text-text" htmlFor="proxmox-server-api-token-secret">
        API token secret
        <Input
          id="proxmox-server-api-token-secret"
          type="password"
          className="mt-1"
          value={form.apiTokenSecret}
          onChange={(event) => updateField("apiTokenSecret", event.target.value)}
          placeholder={mode === "update" ? "Leave blank to keep current" : "Proxmox token secret"}
          disabled={disabled}
        />
      </label>
      <label className="inline-flex items-center gap-2 text-sm text-text md:col-span-2">
        <input
          type="checkbox"
          className="h-4 w-4 rounded border border-input bg-background"
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

  const openCreate = () => {
    setCreateError(null);
    setCreateOpen(true);
  };

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
      toast.success(`Created ${payload.server.name}.`);
      closeCreate();
    } catch (error) {
      setCreateError(null);
      toast.error(toErrorMessage(error, "Unable to create Proxmox server"));
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
      toast.success(`Saved changes for ${payload.server.name}.`);
      closeEdit();
    } catch (error) {
      setEditError(null);
      toast.error(toErrorMessage(error, "Unable to update Proxmox server"));
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
      const deletedServer = selectedServer;
      const response = await fetchWithAuth(`/admin/proxmox/servers/${selectedServer.id}`, { method: "DELETE" });
      await jsonOrThrow<{ ok: boolean }>(response);
      setServers((current) => current.filter((server) => server.id !== selectedServer.id));
      toast.success(`Deleted ${deletedServer?.name ?? "Proxmox server"}.`);
      setSelectedServerId(null);
    } catch (error) {
      toast.error(toErrorMessage(error, "Unable to delete Proxmox server"));
    }
  };

  const validateServer = async (serverId: string) => {
    setValidateBusyId(serverId);

    try {
      const response = await fetchWithAuth(`/admin/proxmox/servers/${serverId}/validate`, { method: "POST" });
      const payload = await jsonOrThrow<ValidateProxmoxServerResponse>(response);
      setValidateResultById((current) => ({ ...current, [serverId]: payload }));
      await loadServers();
    } catch (error) {
      toast.error(toErrorMessage(error, "Unable to validate Proxmox server"));
    } finally {
      setValidateBusyId(null);
    }
  };

  const selectedValidationResult = selectedServer ? validateResultById[selectedServer.id] ?? null : null;

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold text-text">Proxmox servers</h2>
          <p className="text-sm text-muted">Manage Proxmox API connection profiles.</p>
        </div>
        <Button type="button" size="sm" onClick={openCreate}>
          Add server
        </Button>
      </div>

      {loadError ? (
        <Alert tone="destructive">
          <AlertDescription>{loadError}</AlertDescription>
        </Alert>
      ) : null}

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          if (open) {
            setCreateOpen(true);
            return;
          }

          closeCreate();
        }}
      >
        <DialogContent className="max-w-3xl" aria-describedby={undefined}>
          <DialogHeader>
            <DialogTitle>Add Proxmox Server</DialogTitle>
          </DialogHeader>
          <ProxmoxFormFields form={createForm} setForm={setCreateForm} disabled={creating} mode="create" />
          {createError ? (
            <Alert tone="destructive">
              <AlertDescription>{createError}</AlertDescription>
            </Alert>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={closeCreate} disabled={creating}>
              Cancel
            </Button>
            <Button onClick={() => void createServer()} disabled={creating}>
              {creating ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
              ["loading-row-1", "loading-row-2", "loading-row-3"].map((rowKey) => (
                <tr key={rowKey}>
                  <td className="px-3 py-3"><Skeleton className="h-4 w-32" /></td>
                  <td className="px-3 py-3"><Skeleton className="h-4 w-48" /></td>
                  <td className="px-3 py-3"><Skeleton className="h-4 w-24" /></td>
                  <td className="px-3 py-3"><Skeleton className="h-4 w-10" /></td>
                  <td className="px-3 py-3"><Skeleton className="h-4 w-36" /></td>
                  <td className="px-3 py-3"><Skeleton className="h-8 w-24" /></td>
                </tr>
              ))
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
                    <Button type="button" variant="link" size="sm" onClick={() => setSelectedServerId(server.id)}>
                      Manage {server.name}
                    </Button>
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
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void validateServer(selectedServer.id)}
                disabled={validateBusyId === selectedServer.id}
              >
                {validateBusyId === selectedServer.id ? "Validating…" : "Validate"}
              </Button>
              <Button type="button" variant="outline" size="sm" onClick={openEdit}>
                Edit server
              </Button>
              <AlertDialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
                <AlertDialogTrigger asChild>
                  <Button type="button" variant="destructive" size="sm">
                    Delete server
                  </Button>
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

          {selectedValidationResult ? (
            <Alert tone={selectedValidationResult.ok ? "success" : "warning"} className="mt-3">
              <AlertDescription>{selectedValidationResult.message}</AlertDescription>
            </Alert>
          ) : null}
        </div>
      ) : null}

      <Dialog
        open={editOpen && Boolean(selectedServer)}
        onOpenChange={(open) => {
          if (open) {
            setEditOpen(true);
            return;
          }

          closeEdit();
        }}
      >
        <DialogContent className="max-w-3xl" aria-describedby={undefined}>
          <DialogHeader>
            <DialogTitle>Edit Proxmox Server</DialogTitle>
          </DialogHeader>
          {selectedServer ? (
            <>
              <ProxmoxFormFields form={editForm} setForm={setEditForm} disabled={savingEdit} mode="update" />
              {editError ? (
                <Alert tone="destructive">
                  <AlertDescription>{editError}</AlertDescription>
                </Alert>
              ) : null}
              <DialogFooter>
                <Button variant="outline" onClick={closeEdit} disabled={savingEdit}>
                  Cancel
                </Button>
                <Button onClick={() => void saveEdit()} disabled={savingEdit}>
                  {savingEdit ? "Saving…" : "Save changes"}
                </Button>
              </DialogFooter>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </section>
  );
}
