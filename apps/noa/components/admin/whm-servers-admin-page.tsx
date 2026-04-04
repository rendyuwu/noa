"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { formatTimestampUTC } from "@/components/admin/lib/format-timestamp";
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

type WhmServer = {
  id: string;
  name: string;
  base_url: string;
  api_username: string;
  ssh_username: string | null;
  ssh_port: number | null;
  ssh_host_key_fingerprint: string | null;
  has_ssh_password: boolean;
  has_ssh_private_key: boolean;
  verify_ssl: boolean;
  updated_at?: string;
};

type ListWhmServersResponse = {
  servers: WhmServer[];
};

type CreateWhmServerResponse = {
  server: WhmServer;
};

type UpdateWhmServerResponse = {
  server: WhmServer;
};

type ValidateWhmServerResponse = {
  ok: boolean;
  error_code?: string | null;
  message: string;
};

type WhmServerFormState = {
  name: string;
  baseUrl: string;
  apiUsername: string;
  apiToken: string;
  verifySsl: boolean;
  enableSsh: boolean;
  sshUsername: string;
  sshPort: string;
  sshAuthMode: "password" | "private_key";
  sshPassword: string;
  sshPrivateKey: string;
  sshPrivateKeyPassphrase: string;
};

const EMPTY_FORM_STATE: WhmServerFormState = {
  name: "",
  baseUrl: "",
  apiUsername: "",
  apiToken: "",
  verifySsl: true,
  enableSsh: false,
  sshUsername: "",
  sshPort: "22",
  sshAuthMode: "private_key",
  sshPassword: "",
  sshPrivateKey: "",
  sshPrivateKeyPassphrase: "",
};

function parseOptionalPort(value: string): number | null {
  const normalized = value.trim();
  if (!normalized) {
    return null;
  }

  const parsed = Number(normalized);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
    return Number.NaN;
  }

  return parsed;
}

function validateForm(form: WhmServerFormState, mode: "create" | "update", existingServer?: WhmServer | null): string | null {
  if (!form.name.trim()) {
    return "Name is required";
  }

  if (!form.baseUrl.trim()) {
    return "Base URL is required";
  }

  if (!form.apiUsername.trim()) {
    return "API username is required";
  }

  if (mode === "create" && !form.apiToken.trim()) {
    return "API token is required for WHM API operations";
  }

  if (!form.enableSsh) {
    return null;
  }

  const sshPort = parseOptionalPort(form.sshPort);
  if (Number.isNaN(sshPort)) {
    return "SSH port must be between 1 and 65535";
  }

  const hadPassword = existingServer?.has_ssh_password ?? false;
  const hadPrivateKey = existingServer?.has_ssh_private_key ?? false;

  if (form.sshAuthMode === "password") {
    const requiresNewPassword = mode === "create" || !hadPassword || hadPrivateKey;
    if (requiresNewPassword && !form.sshPassword.trim()) {
      return "SSH password is required when password authentication is selected";
    }

    return null;
  }

  const requiresNewPrivateKey = mode === "create" || !hadPrivateKey || hadPassword;
  if (requiresNewPrivateKey && !form.sshPrivateKey.trim()) {
    return "SSH private key is required when SSH key authentication is selected";
  }

  return null;
}

function formStateFromServer(server: WhmServer): WhmServerFormState {
  return {
    name: server.name,
    baseUrl: server.base_url,
    apiUsername: server.api_username,
    apiToken: "",
    verifySsl: server.verify_ssl,
    enableSsh:
      server.has_ssh_password ||
      server.has_ssh_private_key ||
      Boolean(server.ssh_username) ||
      server.ssh_port !== null,
    sshUsername: server.ssh_username ?? "",
    sshPort: server.ssh_port ? String(server.ssh_port) : "22",
    sshAuthMode: server.has_ssh_password ? "password" : "private_key",
    sshPassword: "",
    sshPrivateKey: "",
    sshPrivateKeyPassphrase: "",
  };
}

function buildCreatePayload(form: WhmServerFormState): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    base_url: form.baseUrl.trim(),
    api_username: form.apiUsername.trim(),
    api_token: form.apiToken.trim(),
    verify_ssl: form.verifySsl,
  };

  if (!form.enableSsh) {
    return payload;
  }

  const sshUsername = form.sshUsername.trim();
  const sshPort = parseOptionalPort(form.sshPort);
  if (sshUsername) {
    payload.ssh_username = sshUsername;
  }
  if (sshPort !== null && !Number.isNaN(sshPort)) {
    payload.ssh_port = sshPort;
  }

  if (form.sshAuthMode === "password") {
    payload.ssh_password = form.sshPassword.trim();
  } else {
    payload.ssh_private_key = form.sshPrivateKey.trim();
    const passphrase = form.sshPrivateKeyPassphrase.trim();
    if (passphrase) {
      payload.ssh_private_key_passphrase = passphrase;
    }
  }

  return payload;
}

function buildUpdatePayload(form: WhmServerFormState, existingServer: WhmServer): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    base_url: form.baseUrl.trim(),
    api_username: form.apiUsername.trim(),
    verify_ssl: form.verifySsl,
  };

  if (form.apiToken.trim()) {
    payload.api_token = form.apiToken.trim();
  }

  const hadSshConfig =
    existingServer.has_ssh_password ||
    existingServer.has_ssh_private_key ||
    Boolean(existingServer.ssh_username) ||
    existingServer.ssh_port !== null ||
    Boolean(existingServer.ssh_host_key_fingerprint);

  if (!form.enableSsh) {
    if (hadSshConfig) {
      payload.clear_ssh_configuration = true;
    }
    return payload;
  }

  const normalizedUsername = form.sshUsername.trim();
  if (normalizedUsername) {
    payload.ssh_username = normalizedUsername;
  } else if (existingServer.ssh_username) {
    payload.clear_ssh_username = true;
  }

  const sshPort = parseOptionalPort(form.sshPort);
  if (sshPort !== null && !Number.isNaN(sshPort)) {
    payload.ssh_port = sshPort;
  } else if (existingServer.ssh_port !== null) {
    payload.clear_ssh_port = true;
  }

  if (form.sshAuthMode === "password") {
    if (existingServer.has_ssh_private_key) {
      payload.clear_ssh_private_key = true;
      payload.clear_ssh_private_key_passphrase = true;
    }
    if (form.sshPassword.trim()) {
      payload.ssh_password = form.sshPassword.trim();
    }
  } else {
    if (existingServer.has_ssh_password) {
      payload.clear_ssh_password = true;
    }
    if (form.sshPrivateKey.trim()) {
      payload.ssh_private_key = form.sshPrivateKey.trim();
      if (form.sshPrivateKeyPassphrase.trim()) {
        payload.ssh_private_key_passphrase = form.sshPrivateKeyPassphrase.trim();
      } else {
        payload.clear_ssh_private_key_passphrase = true;
      }
    }
  }

  return payload;
}

function WhmServerFormFields({
  form,
  setForm,
  disabled,
  mode,
}: {
  form: WhmServerFormState;
  setForm: (updater: (current: WhmServerFormState) => WhmServerFormState) => void;
  disabled: boolean;
  mode: "create" | "update";
}) {
  const updateField = <K extends keyof WhmServerFormState>(key: K, value: WhmServerFormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const fieldId = (field: string) => `${mode}-whm-${field}`;

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <label className="text-sm text-text" htmlFor={fieldId("name")}>
        Name
        <Input
          id={fieldId("name")}
          className="mt-1"
          value={form.name}
          onChange={(event) => updateField("name", event.target.value)}
          disabled={disabled}
        />
      </label>
      <label className="text-sm text-text" htmlFor={fieldId("base-url")}>
        Base URL
        <Input
          id={fieldId("base-url")}
          className="mt-1"
          value={form.baseUrl}
          onChange={(event) => updateField("baseUrl", event.target.value)}
          disabled={disabled}
        />
      </label>
      <label className="text-sm text-text" htmlFor={fieldId("api-username")}>
        API username
        <Input
          id={fieldId("api-username")}
          className="mt-1"
          value={form.apiUsername}
          onChange={(event) => updateField("apiUsername", event.target.value)}
          disabled={disabled}
        />
      </label>
      <label className="text-sm text-text" htmlFor={fieldId("api-token")}>
        API token
        <Input
          id={fieldId("api-token")}
          type="password"
          className="mt-1"
          value={form.apiToken}
          onChange={(event) => updateField("apiToken", event.target.value)}
          placeholder={mode === "update" ? "Leave blank to keep current" : "WHM token"}
          disabled={disabled}
        />
      </label>
      <label className="inline-flex items-center gap-2 text-sm text-text" htmlFor={fieldId("verify-ssl")}>
        <input
          id={fieldId("verify-ssl")}
          type="checkbox"
          checked={form.verifySsl}
          onChange={(event) => updateField("verifySsl", event.target.checked)}
          disabled={disabled}
        />
        Verify SSL
      </label>
      <label className="inline-flex items-center gap-2 text-sm text-text" htmlFor={fieldId("enable-ssh")}>
        <input
          id={fieldId("enable-ssh")}
          type="checkbox"
          checked={form.enableSsh}
          onChange={(event) => updateField("enableSsh", event.target.checked)}
          disabled={disabled}
        />
        Enable SSH
      </label>

      {form.enableSsh ? (
        <>
          <label className="text-sm text-text" htmlFor={fieldId("ssh-username")}>
            SSH username
            <Input
              id={fieldId("ssh-username")}
              className="mt-1"
              value={form.sshUsername}
              onChange={(event) => updateField("sshUsername", event.target.value)}
              disabled={disabled}
            />
          </label>
          <label className="text-sm text-text" htmlFor={fieldId("ssh-port")}>
            SSH port
            <Input
              id={fieldId("ssh-port")}
              className="mt-1"
              value={form.sshPort}
              onChange={(event) => updateField("sshPort", event.target.value)}
              disabled={disabled}
            />
          </label>

          <fieldset className="rounded-xl border border-border p-3 md:col-span-2">
            <legend className="px-1 text-xs uppercase tracking-[0.08em] text-muted">SSH authentication</legend>
            <div className="mt-2 flex items-center gap-4 text-sm">
              <label className="inline-flex items-center gap-2" htmlFor={fieldId("ssh-auth-private-key")}>
                <input
                  id={fieldId("ssh-auth-private-key")}
                  type="radio"
                  name={`${mode}-ssh-auth`}
                  checked={form.sshAuthMode === "private_key"}
                  onChange={() => updateField("sshAuthMode", "private_key")}
                  disabled={disabled}
                />
                Private key
              </label>
              <label className="inline-flex items-center gap-2" htmlFor={fieldId("ssh-auth-password")}>
                <input
                  id={fieldId("ssh-auth-password")}
                  type="radio"
                  name={`${mode}-ssh-auth`}
                  checked={form.sshAuthMode === "password"}
                  onChange={() => updateField("sshAuthMode", "password")}
                  disabled={disabled}
                />
                Password
              </label>
            </div>

            {form.sshAuthMode === "password" ? (
              <label className="mt-3 block text-sm text-text" htmlFor={fieldId("ssh-password")}>
                SSH password
                <Input
                  id={fieldId("ssh-password")}
                  type="password"
                  className="mt-1"
                  value={form.sshPassword}
                  onChange={(event) => updateField("sshPassword", event.target.value)}
                  disabled={disabled}
                />
              </label>
            ) : (
              <>
                <label className="mt-3 block text-sm text-text" htmlFor={fieldId("ssh-private-key")}>
                  SSH private key
                  <textarea
                    id={fieldId("ssh-private-key")}
                    className="mt-1 min-h-24 w-full rounded-md border border-input bg-background px-3 py-2 text-base ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 md:text-sm"
                    value={form.sshPrivateKey}
                    onChange={(event) => updateField("sshPrivateKey", event.target.value)}
                    disabled={disabled}
                  />
                </label>
                <label className="mt-3 block text-sm text-text" htmlFor={fieldId("ssh-private-key-passphrase")}>
                  Key passphrase
                  <Input
                    id={fieldId("ssh-private-key-passphrase")}
                    type="password"
                    className="mt-1"
                    value={form.sshPrivateKeyPassphrase}
                    onChange={(event) => updateField("sshPrivateKeyPassphrase", event.target.value)}
                    disabled={disabled}
                  />
                </label>
              </>
            )}
          </fieldset>
        </>
      ) : null}
    </div>
  );
}

export function WhmServersAdminPage() {
  const [servers, setServers] = useState<WhmServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selectedServerId, setSelectedServerId] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
  const [createForm, setCreateForm] = useState<WhmServerFormState>(EMPTY_FORM_STATE);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [editOpen, setEditOpen] = useState(false);
  const [editForm, setEditForm] = useState<WhmServerFormState>(EMPTY_FORM_STATE);
  const [savingEdit, setSavingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [validateBusyId, setValidateBusyId] = useState<string | null>(null);
  const [validateResultById, setValidateResultById] = useState<Record<string, ValidateWhmServerResponse>>({});

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
      const response = await fetchWithAuth("/admin/whm/servers");
      const payload = await jsonOrThrow<ListWhmServersResponse>(response);
      const nextServers = Array.isArray(payload.servers) ? payload.servers : [];
      setServers(nextServers);
      setSelectedServerId((current) => {
        if (current && nextServers.some((server) => server.id === current)) {
          return current;
        }

        return nextServers[0]?.id ?? null;
      });
    } catch (error) {
      setLoadError(toErrorMessage(error, "Unable to load WHM servers"));
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

  const closeEdit = () => {
    setEditOpen(false);
    setEditError(null);
  };

  const closeCreate = () => {
    setCreateOpen(false);
    setCreateError(null);
    setCreateForm(EMPTY_FORM_STATE);
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
      const response = await fetchWithAuth("/admin/whm/servers", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(buildCreatePayload(createForm)),
      });
      const payload = await jsonOrThrow<CreateWhmServerResponse>(response);

      setServers((current) => [payload.server, ...current]);
      setSelectedServerId(payload.server.id);
      toast.success(`Created ${payload.server.name}.`);
      closeCreate();
    } catch (error) {
      setCreateError(null);
      toast.error(toErrorMessage(error, "Unable to create WHM server"));
    } finally {
      setCreating(false);
    }
  };

  const saveEdit = async () => {
    if (!selectedServer) {
      return;
    }

    const validationError = validateForm(editForm, "update", selectedServer);
    if (validationError) {
      setEditError(validationError);
      return;
    }

    setSavingEdit(true);
    setEditError(null);

    try {
      const response = await fetchWithAuth(`/admin/whm/servers/${selectedServer.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(buildUpdatePayload(editForm, selectedServer)),
      });
      const payload = await jsonOrThrow<UpdateWhmServerResponse>(response);

      setServers((current) => current.map((server) => (server.id === payload.server.id ? payload.server : server)));
      toast.success(`Saved changes for ${payload.server.name}.`);
      closeEdit();
    } catch (error) {
      setEditError(null);
      toast.error(toErrorMessage(error, "Unable to update WHM server"));
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
      const response = await fetchWithAuth(`/admin/whm/servers/${selectedServer.id}`, { method: "DELETE" });
      await jsonOrThrow<{ ok: boolean }>(response);
      setServers((current) => current.filter((server) => server.id !== selectedServer.id));
      toast.success(`Deleted ${deletedServer?.name ?? "WHM server"}.`);
      setSelectedServerId(null);
    } catch (error) {
      toast.error(toErrorMessage(error, "Unable to delete WHM server"));
    }
  };

  const validateServer = async (serverId: string) => {
    setValidateBusyId(serverId);

    try {
      const response = await fetchWithAuth(`/admin/whm/servers/${serverId}/validate`, {
        method: "POST",
      });
      const payload = await jsonOrThrow<ValidateWhmServerResponse>(response);
      setValidateResultById((current) => ({ ...current, [serverId]: payload }));
      await loadServers();
    } catch (error) {
      toast.error(toErrorMessage(error, "Unable to validate WHM server"));
    } finally {
      setValidateBusyId(null);
    }
  };

  const selectedValidationResult = selectedServer ? validateResultById[selectedServer.id] ?? null : null;

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold text-text">WHM servers</h2>
          <p className="text-sm text-muted">Manage WHM API + SSH connection profiles.</p>
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
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
            <DialogTitle>Add WHM Server</DialogTitle>
          </DialogHeader>
          <WhmServerFormFields form={createForm} setForm={setCreateForm} disabled={creating} mode="create" />
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
              <th className="px-3 py-2">API user</th>
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
                  No WHM servers configured.
                </td>
              </tr>
            ) : (
              sortedServers.map((server) => (
                <tr
                  key={server.id}
                  className={`transition-colors hover:bg-surface-2/50${selectedServerId === server.id ? " bg-accent/5" : ""}`}
                  onClick={() => setSelectedServerId(server.id)}
                >
                  <td className="px-3 py-3 font-medium text-text">{server.name}</td>
                  <td className="px-3 py-3 text-muted">{server.base_url}</td>
                  <td className="px-3 py-3 text-text">{server.api_username}</td>
                  <td className="px-3 py-3 text-text">{server.verify_ssl ? "on" : "off"}</td>
                  <td className="px-3 py-3 text-muted">{formatTimestampUTC(server.updated_at)}</td>
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
                  <Button type="button" variant="destructive-outline" size="sm">
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
            <DialogTitle>Edit WHM Server</DialogTitle>
          </DialogHeader>
          {selectedServer ? (
            <>
              <WhmServerFormFields form={editForm} setForm={setEditForm} disabled={savingEdit} mode="update" />
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
