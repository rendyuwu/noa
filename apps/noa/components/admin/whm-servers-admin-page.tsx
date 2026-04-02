"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchWithAuth, jsonOrThrow } from "@/components/lib/http/fetch-client";
import { toErrorMessage } from "@/components/lib/http/error-message";

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
        API username
        <input
          className={inputClass}
          value={form.apiUsername}
          onChange={(event) => updateField("apiUsername", event.target.value)}
          disabled={disabled}
        />
      </label>
      <label className="text-sm text-text">
        API token
        <input
          type="password"
          className={inputClass}
          value={form.apiToken}
          onChange={(event) => updateField("apiToken", event.target.value)}
          placeholder={mode === "update" ? "Leave blank to keep current" : "WHM token"}
          disabled={disabled}
        />
      </label>
      <label className="inline-flex items-center gap-2 text-sm text-text">
        <input
          type="checkbox"
          checked={form.verifySsl}
          onChange={(event) => updateField("verifySsl", event.target.checked)}
          disabled={disabled}
        />
        Verify SSL
      </label>
      <label className="inline-flex items-center gap-2 text-sm text-text">
        <input
          type="checkbox"
          checked={form.enableSsh}
          onChange={(event) => updateField("enableSsh", event.target.checked)}
          disabled={disabled}
        />
        Enable SSH
      </label>

      {form.enableSsh ? (
        <>
          <label className="text-sm text-text">
            SSH username
            <input
              className={inputClass}
              value={form.sshUsername}
              onChange={(event) => updateField("sshUsername", event.target.value)}
              disabled={disabled}
            />
          </label>
          <label className="text-sm text-text">
            SSH port
            <input
              className={inputClass}
              value={form.sshPort}
              onChange={(event) => updateField("sshPort", event.target.value)}
              disabled={disabled}
            />
          </label>

          <fieldset className="rounded-xl border border-border p-3 md:col-span-2">
            <legend className="px-1 text-xs uppercase tracking-[0.08em] text-muted">SSH authentication</legend>
            <div className="mt-2 flex items-center gap-4 text-sm">
              <label className="inline-flex items-center gap-2">
                <input
                  type="radio"
                  name={`${mode}-ssh-auth`}
                  checked={form.sshAuthMode === "private_key"}
                  onChange={() => updateField("sshAuthMode", "private_key")}
                  disabled={disabled}
                />
                Private key
              </label>
              <label className="inline-flex items-center gap-2">
                <input
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
              <label className="mt-3 block text-sm text-text">
                SSH password
                <input
                  type="password"
                  className={inputClass}
                  value={form.sshPassword}
                  onChange={(event) => updateField("sshPassword", event.target.value)}
                  disabled={disabled}
                />
              </label>
            ) : (
              <>
                <label className="mt-3 block text-sm text-text">
                  SSH private key
                  <textarea
                    className={`${inputClass} min-h-24`}
                    value={form.sshPrivateKey}
                    onChange={(event) => updateField("sshPrivateKey", event.target.value)}
                    disabled={disabled}
                  />
                </label>
                <label className="mt-3 block text-sm text-text">
                  Key passphrase
                  <input
                    type="password"
                    className={inputClass}
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
  const [actionStatus, setActionStatus] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [selectedServerId, setSelectedServerId] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
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
      setActionStatus(`Created ${payload.server.name}.`);
      closeCreate();
    } catch (error) {
      setCreateError(toErrorMessage(error, "Unable to create WHM server"));
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
      setActionStatus(`Saved changes for ${payload.server.name}.`);
      closeEdit();
    } catch (error) {
      setEditError(toErrorMessage(error, "Unable to update WHM server"));
    } finally {
      setSavingEdit(false);
    }
  };

  const deleteServer = async () => {
    if (!selectedServer) {
      return;
    }

    const confirmed = window.confirm(`Delete ${selectedServer.name}?`);
    if (!confirmed) {
      return;
    }

    try {
      const response = await fetchWithAuth(`/admin/whm/servers/${selectedServer.id}`, { method: "DELETE" });
      await jsonOrThrow<{ ok: boolean }>(response);
      setServers((current) => current.filter((server) => server.id !== selectedServer.id));
      setActionStatus(`Deleted ${selectedServer.name}.`);
      setSelectedServerId(null);
    } catch (error) {
      setActionError(toErrorMessage(error, "Unable to delete WHM server"));
    }
  };

  const validateServer = async (serverId: string) => {
    setValidateBusyId(serverId);
    setActionError(null);

    try {
      const response = await fetchWithAuth(`/admin/whm/servers/${serverId}/validate`, {
        method: "POST",
      });
      const payload = await jsonOrThrow<ValidateWhmServerResponse>(response);
      setValidateResultById((current) => ({ ...current, [serverId]: payload }));
      await loadServers();
    } catch (error) {
      setActionError(toErrorMessage(error, "Unable to validate WHM server"));
    } finally {
      setValidateBusyId(null);
    }
  };

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold text-text">WHM servers</h2>
          <p className="text-sm text-muted">Manage WHM API + SSH connection profiles.</p>
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
            <WhmServerFormFields form={createForm} setForm={setCreateForm} disabled={creating} mode="create" />
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
              <th className="px-3 py-2">API user</th>
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
                  No WHM servers configured.
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
                  <td className="px-3 py-3 text-text">{server.api_username}</td>
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
              <button
                type="button"
                className="rounded-xl border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800"
                onClick={() => void deleteServer()}
              >
                Delete server
              </button>
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
            <WhmServerFormFields form={editForm} setForm={setEditForm} disabled={savingEdit} mode="update" />
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
