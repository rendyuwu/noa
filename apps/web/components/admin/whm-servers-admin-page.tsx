"use client";

import type { Dispatch, SetStateAction } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import * as Dialog from "@radix-ui/react-dialog";
import { Cross2Icon } from "@radix-ui/react-icons";

import { Button } from "@/components/ui/button";
import { ConfirmAction } from "@/components/lib/confirm-dialog";
import { toUserMessage } from "@/components/lib/error-message";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";
import { ScrollArea } from "@/components/lib/scroll-area";

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
  created_at?: string;
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

type SshAuthMode = "password" | "private_key";

type WhmServerFormState = {
  name: string;
  baseUrl: string;
  apiUsername: string;
  apiToken: string;
  verifySsl: boolean;
  enableSsh: boolean;
  sshUsername: string;
  sshPort: string;
  sshAuthMode: SshAuthMode;
  sshPassword: string;
  sshPrivateKey: string;
  sshPrivateKeyPassphrase: string;
};

const labelClass = "block text-sm font-medium text-foreground";
const inputClass =
  "mt-1 w-full rounded-xl border border-border bg-card/80 px-3 py-2.5 text-sm text-foreground shadow-sm outline-none placeholder:text-muted focus-visible:border-primary/60 focus-visible:ring-2 focus-visible:ring-primary/25 focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-70";
const helperClass = "mt-1 font-sans text-xs text-muted";

const EMPTY_FORM_STATE: WhmServerFormState = {
  name: "",
  baseUrl: "",
  apiUsername: "",
  apiToken: "",
  verifySsl: true,
  enableSsh: false,
  sshUsername: "",
  sshPort: "",
  sshAuthMode: "private_key",
  sshPassword: "",
  sshPrivateKey: "",
  sshPrivateKeyPassphrase: "",
};

function formatTimestamp(value: unknown): string {
  if (typeof value !== "string" || !value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "Z");
}

function getSshStatus(server: WhmServer | null): { label: string; className: string } {
  if (!server) return { label: "Not configured", className: "status-badge" };
  if (server.has_ssh_password || server.has_ssh_private_key) {
    return { label: "Configured", className: "status-badge-success" };
  }
  return { label: "Not configured", className: "status-badge" };
}

function getSshAuthLabel(server: WhmServer | null): string {
  if (!server) return "-";
  if (server.has_ssh_private_key) return "SSH key";
  if (server.has_ssh_password) return "Password";
  return "-";
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
    sshPort: server.ssh_port != null ? String(server.ssh_port) : "",
    sshAuthMode: server.has_ssh_private_key ? "private_key" : "password",
    sshPassword: "",
    sshPrivateKey: "",
    sshPrivateKeyPassphrase: "",
  };
}

function parseOptionalPort(value: string): number | null {
  const normalized = value.trim();
  if (!normalized) return null;
  const parsed = Number(normalized);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
    return Number.NaN;
  }
  return parsed;
}

function validateForm(
  form: WhmServerFormState,
  mode: "create" | "update",
  existingServer?: WhmServer | null,
): string | null {
  if (!form.name.trim()) return "Name is required";
  if (!form.baseUrl.trim()) return "Base URL is required";
  if (!form.apiUsername.trim()) return "API username is required";
  if (mode === "create" && !form.apiToken.trim()) {
    return "API token is required for WHM API operations";
  }

  if (!form.enableSsh) return null;

  const sshPort = parseOptionalPort(form.sshPort);
  if (Number.isNaN(sshPort)) return "SSH port must be between 1 and 65535";

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

function buildCreatePayload(form: WhmServerFormState): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    name: form.name.trim(),
    base_url: form.baseUrl.trim(),
    api_username: form.apiUsername.trim(),
    api_token: form.apiToken.trim(),
    verify_ssl: form.verifySsl,
  };

  if (!form.enableSsh) return payload;

  const sshUsername = form.sshUsername.trim();
  const sshPort = parseOptionalPort(form.sshPort);
  if (sshUsername) payload.ssh_username = sshUsername;
  if (sshPort !== null && !Number.isNaN(sshPort)) payload.ssh_port = sshPort;

  if (form.sshAuthMode === "password") {
    payload.ssh_password = form.sshPassword.trim();
  } else {
    payload.ssh_private_key = form.sshPrivateKey.trim();
    const passphrase = form.sshPrivateKeyPassphrase.trim();
    if (passphrase) payload.ssh_private_key_passphrase = passphrase;
  }

  return payload;
}

function buildUpdatePayload(
  form: WhmServerFormState,
  existingServer: WhmServer,
): Record<string, unknown> {
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
    if (hadSshConfig) payload.clear_ssh_configuration = true;
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

type FormFieldsProps = {
  form: WhmServerFormState;
  setForm: Dispatch<SetStateAction<WhmServerFormState>>;
  disabled: boolean;
  mode: "create" | "update";
  existingServer?: WhmServer | null;
};

function WhmServerFormFields({ form, setForm, disabled, mode, existingServer }: FormFieldsProps) {
  const updateForm = <K extends keyof WhmServerFormState>(key: K, value: WhmServerFormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const storedSshCopy =
    mode === "update"
      ? "Leave secret fields blank to keep the stored values. Enter a new value to replace them. CSF and firewall tools use these SSH credentials."
      : "SSH is optional, but required for CSF and other SSH-backed tools.";

  return (
    <div className="grid gap-4">
      <div className="rounded-xl border border-border bg-card/50 px-4 py-4">
        <div className="text-xs font-semibold uppercase tracking-wide text-muted">WHM API</div>
        <div className="mt-3 grid gap-4">
          <div>
            <label className={labelClass} htmlFor={`${mode}-whm-name`}>
              Name
            </label>
            <input
              id={`${mode}-whm-name`}
              className={inputClass}
              value={form.name}
              onChange={(event) => updateForm("name", event.target.value)}
              placeholder="web1"
              required
              disabled={disabled}
            />
          </div>

          <div>
            <label className={labelClass} htmlFor={`${mode}-whm-base-url`}>
              Base URL
            </label>
            <input
              id={`${mode}-whm-base-url`}
              className={inputClass}
              value={form.baseUrl}
              onChange={(event) => updateForm("baseUrl", event.target.value)}
              placeholder="https://whm.example.com:2087"
              required
              disabled={disabled}
            />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className={labelClass} htmlFor={`${mode}-whm-api-username`}>
                API username
              </label>
              <input
                id={`${mode}-whm-api-username`}
                className={inputClass}
                value={form.apiUsername}
                onChange={(event) => updateForm("apiUsername", event.target.value)}
                placeholder="root"
                required
                disabled={disabled}
              />
            </div>
            <div className="flex items-center gap-2 pt-7">
              <input
                id={`${mode}-whm-verify-ssl`}
                type="checkbox"
                checked={form.verifySsl}
                onChange={(event) => updateForm("verifySsl", event.target.checked)}
                disabled={disabled}
              />
              <label htmlFor={`${mode}-whm-verify-ssl`} className="text-sm text-foreground">
                Verify SSL
              </label>
            </div>
          </div>

          <div>
            <label className={labelClass} htmlFor={`${mode}-whm-api-token`}>
              API token
            </label>
            <input
              id={`${mode}-whm-api-token`}
              type="password"
              className={inputClass}
              value={form.apiToken}
              onChange={(event) => updateForm("apiToken", event.target.value)}
              placeholder={mode === "create" ? "••••••••••" : "Stored — enter a new token to replace"}
              required={mode === "create"}
              disabled={disabled}
            />
            {mode === "update" ? (
              <p className={helperClass}>Leave blank to keep the stored API token for WHM API tools.</p>
            ) : null}
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-card/50 px-4 py-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-muted">SSH access</div>
            <p className={helperClass}>Used for CSF, firewall, and other SSH-backed server tools.</p>
          </div>
          <label className="flex items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={form.enableSsh}
              onChange={(event) => updateForm("enableSsh", event.target.checked)}
              disabled={disabled}
            />
            Enable SSH
          </label>
        </div>

        {form.enableSsh ? (
          <div className="mt-3 grid gap-4">
            <p className={helperClass}>{storedSshCopy}</p>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className={labelClass} htmlFor={`${mode}-whm-ssh-username`}>
                  SSH username
                </label>
                <input
                  id={`${mode}-whm-ssh-username`}
                  className={inputClass}
                  value={form.sshUsername}
                  onChange={(event) => updateForm("sshUsername", event.target.value)}
                  placeholder={existingServer?.ssh_username ?? "root (default)"}
                  disabled={disabled}
                />
                <p className={helperClass}>Leave blank to fall back to root.</p>
              </div>
              <div>
                <label className={labelClass} htmlFor={`${mode}-whm-ssh-port`}>
                  SSH port
                </label>
                <input
                  id={`${mode}-whm-ssh-port`}
                  type="number"
                  min={1}
                  max={65535}
                  className={inputClass}
                  value={form.sshPort}
                  onChange={(event) => updateForm("sshPort", event.target.value)}
                  placeholder={existingServer?.ssh_port != null ? String(existingServer.ssh_port) : "22"}
                  disabled={disabled}
                />
                <p className={helperClass}>Leave blank to use port 22.</p>
              </div>
            </div>

            <fieldset>
              <legend className={labelClass}>Authentication</legend>
              <div className="mt-2 flex flex-wrap gap-4 text-sm text-foreground">
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name={`${mode}-ssh-auth-mode`}
                    checked={form.sshAuthMode === "private_key"}
                    onChange={() => updateForm("sshAuthMode", "private_key")}
                    disabled={disabled}
                  />
                  SSH key
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name={`${mode}-ssh-auth-mode`}
                    checked={form.sshAuthMode === "password"}
                    onChange={() => updateForm("sshAuthMode", "password")}
                    disabled={disabled}
                  />
                  Password
                </label>
              </div>
            </fieldset>

            {form.sshAuthMode === "password" ? (
              <div>
                <label className={labelClass} htmlFor={`${mode}-whm-ssh-password`}>
                  SSH password
                </label>
                <input
                  id={`${mode}-whm-ssh-password`}
                  type="password"
                  className={inputClass}
                  value={form.sshPassword}
                  onChange={(event) => updateForm("sshPassword", event.target.value)}
                  placeholder={mode === "create" ? "••••••••••" : "Stored — enter a new password to replace"}
                  disabled={disabled}
                />
              </div>
            ) : (
              <>
                <div>
                  <label className={labelClass} htmlFor={`${mode}-whm-ssh-private-key`}>
                    SSH private key
                  </label>
                  <textarea
                    id={`${mode}-whm-ssh-private-key`}
                    className={`${inputClass} min-h-32 font-mono text-xs`}
                    value={form.sshPrivateKey}
                    onChange={(event) => updateForm("sshPrivateKey", event.target.value)}
                    placeholder={
                      mode === "create"
                        ? "-----BEGIN OPENSSH PRIVATE KEY-----"
                        : "Stored — paste a new private key to replace"
                    }
                    disabled={disabled}
                  />
                </div>
                <div>
                  <label className={labelClass} htmlFor={`${mode}-whm-ssh-private-key-passphrase`}>
                    Key passphrase
                  </label>
                  <input
                    id={`${mode}-whm-ssh-private-key-passphrase`}
                    type="password"
                    className={inputClass}
                    value={form.sshPrivateKeyPassphrase}
                    onChange={(event) => updateForm("sshPrivateKeyPassphrase", event.target.value)}
                    placeholder={mode === "create" ? "Optional" : "Stored — enter a new passphrase to replace"}
                    disabled={disabled}
                  />
                  <p className={helperClass}>Optional. If you replace the private key and leave this blank, the stored passphrase is cleared.</p>
                </div>
              </>
            )}
          </div>
        ) : (
          <p className="mt-3 text-sm text-muted">Not configured. CSF, firewall, and other SSH-backed tools will be unavailable until you add SSH credentials and run Validate.</p>
        )}
      </div>
    </div>
  );
}

export function WhmServersAdminPage() {
  const [servers, setServers] = useState<WhmServer[]>([]);
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
  const [createForm, setCreateForm] = useState<WhmServerFormState>(EMPTY_FORM_STATE);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const [editOpen, setEditOpen] = useState(false);
  const [editForm, setEditForm] = useState<WhmServerFormState>(EMPTY_FORM_STATE);
  const [savingEdit, setSavingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  const [validateBusyId, setValidateBusyId] = useState<string | null>(null);
  const [validateResultById, setValidateResultById] = useState<Record<string, ValidateWhmServerResponse>>({});
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

  const openPanelForServer = (server: WhmServer, opener: HTMLElement | null) => {
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
      const response = await fetchWithAuth("/admin/whm/servers", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify(buildCreatePayload(createForm)),
      });

      const payload = await jsonOrThrow<CreateWhmServerResponse>(response);
      setServers((prev) => [...prev, payload.server]);
      setCreateOpen(false);
      resetCreateForm();
      setActionError(null);
      setActionStatus(`Saved ${payload.server.name}.`);
    } catch (error) {
      setCreateError(toUserMessage(error, "Unable to create WHM server"));
    } finally {
      setCreating(false);
      setCreateForm((prev) => ({ ...prev, apiToken: "", sshPassword: "", sshPrivateKey: "", sshPrivateKeyPassphrase: "" }));
    }
  };

  const updateServer = async () => {
    if (!selectedServer) return;

    const validationError = validateForm(editForm, "update", selectedServer);
    if (validationError) {
      setEditError(validationError);
      return;
    }

    setSavingEdit(true);
    setEditError(null);
    setActionStatus(null);

    try {
      const response = await fetchWithAuth(`/admin/whm/servers/${selectedServer.id}`, {
        method: "PATCH",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify(buildUpdatePayload(editForm, selectedServer)),
      });

      const payload = await jsonOrThrow<UpdateWhmServerResponse>(response);
      setServers((prev) => prev.map((server) => (server.id === payload.server.id ? payload.server : server)));
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
      setEditError(toUserMessage(error, "Unable to update WHM server"));
    } finally {
      setSavingEdit(false);
      setEditForm((prev) => ({ ...prev, apiToken: "", sshPassword: "", sshPrivateKey: "", sshPrivateKeyPassphrase: "" }));
    }
  };

  const validateServer = async (serverId: string) => {
    if (!serverId) return;
    setValidateBusyId(serverId);
    setActionError(null);
    setActionStatus(null);

    try {
      const response = await fetchWithAuth(`/admin/whm/servers/${serverId}/validate`, {
        method: "POST",
      });
      const payload = await jsonOrThrow<ValidateWhmServerResponse>(response);
      setValidateResultById((prev) => ({ ...prev, [serverId]: payload }));
      if (payload.ok) {
        setActionStatus(payload.message && payload.message !== "ok" ? payload.message : "Validation succeeded.");
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
      setActionStatus("WHM server deleted.");
      if (panelStillMatches(seq, serverId)) {
        closePanel();
      }
    } catch (error) {
      if (panelStillMatches(seq, serverId)) {
        setActionError(toUserMessage(error, "Unable to delete WHM server"));
      }
    } finally {
      setDeleteBusyId((prev) => (prev === serverId ? null : prev));
    }
  };

  const sshStatus = getSshStatus(selectedServer);

  return (
    <>
      <Dialog.Root open={createOpen} onOpenChange={setCreateOpen}>
        <main className="min-h-dvh bg-background p-6">
          <div className="flex items-end justify-between gap-3">
            <div>
              <h1 className="text-2xl font-semibold">WHM Servers</h1>
              <p className="mt-1 font-sans text-sm text-muted">
                Store WHM API and SSH credentials in NOA. CSF and firewall tools run over SSH, and secrets are never shown after save.
              </p>
            </div>

            <div className="flex items-center gap-2">
              <Button disabled={loading} onClick={() => void loadServers()} size="sm">
                Refresh
              </Button>
              <Dialog.Trigger asChild>
                <Button onClick={openCreate} variant="default" size="sm">
                  Add server
                </Button>
              </Dialog.Trigger>
            </div>
          </div>

          {loadError ? (
            <div
              role="alert"
              aria-live="assertive"
              className="mt-4 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 font-sans text-sm text-destructive"
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
              className="mt-4 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 font-sans text-sm text-destructive"
            >
              {actionError}
            </div>
          ) : null}

          {actionStatus ? (
            <div
              role="status"
              aria-live="polite"
              className="mt-4 rounded-xl border border-success/25 bg-success/10 px-3 py-2 font-sans text-sm text-success"
            >
              {actionStatus}
            </div>
          ) : null}

          <div className="panel mt-6 overflow-hidden">
            <table className="w-full font-sans text-sm">
              <thead className="bg-accent text-muted">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Name</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Base URL</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">API user</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">SSL</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Updated</th>
                  <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide">Manage</th>
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
                        className="cursor-pointer transition-colors hover:bg-primary/60 focus-visible:bg-primary/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent/40"
                        onClick={(event) => openPanelForServer(server, event.currentTarget)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
                            event.preventDefault();
                            openPanelForServer(server, event.currentTarget);
                          }
                        }}
                      >
                        <td className="px-4 py-3 text-foreground">
                          <div className="font-medium text-foreground">{server.name}</div>
                          <div className="mt-1 flex flex-wrap items-center gap-2">
                            {badge ? <span className={["status-badge", badge.className].join(" ")}>{badge.label}</span> : null}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-muted">{server.base_url}</td>
                        <td className="px-4 py-3 text-muted">{server.api_username}</td>
                        <td className="px-4 py-3 text-muted">{server.verify_ssl ? "on" : "off"}</td>
                        <td className="px-4 py-3 text-muted">{formatTimestamp(server.updated_at)}</td>
                        <td className="px-4 py-3 text-muted">Manage</td>
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

            <Dialog.Content className="fixed top-1/2 left-1/2 z-50 flex max-h-[90vh] w-[min(92vw,760px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-xl outline-none">
              <div className="flex items-start justify-between gap-3 border-b border-border bg-card/50 px-5 py-4">
                <div className="min-w-0">
                  <Dialog.Title className="text-lg font-semibold text-foreground">Add WHM server</Dialog.Title>
                  <Dialog.Description className="mt-1 font-sans text-sm text-muted">
                    WHM API token and SSH credentials are stored securely and never displayed again. CSF and firewall tools use the SSH path.
                  </Dialog.Description>
                </div>
                <Dialog.Close asChild>
                  <Button aria-label="Close" className="text-muted hover:text-foreground" size="icon">
                    <Cross2Icon width={18} height={18} />
                  </Button>
                </Dialog.Close>
              </div>



              <ScrollArea className="min-h-0 flex-1" viewportClassName="h-full px-5 py-4 font-sans">
                <WhmServerFormFields form={createForm} setForm={setCreateForm} disabled={creating} mode="create" />

                {createError ? (
                  <p
                    role="alert"
                    aria-live="assertive"
                    className="mt-4 rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive"
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
                <Button disabled={creating} onClick={() => void createServer()} size="sm" variant="default">
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
          <Dialog.Content className="fixed top-1/2 left-1/2 z-[60] flex max-h-[90vh] w-[min(92vw,760px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-xl outline-none">
            <div className="flex items-start justify-between gap-3 border-b border-border bg-card/50 px-5 py-4">
              <div className="min-w-0">
                <Dialog.Title className="text-lg font-semibold text-foreground">Edit WHM server</Dialog.Title>
                <Dialog.Description className="mt-1 font-sans text-sm text-muted">
                  Stored secrets can be replaced, but they are never shown again.
                </Dialog.Description>
              </div>
              <Dialog.Close asChild>
                <Button aria-label="Close" className="text-muted hover:text-foreground" size="icon">
                  <Cross2Icon width={18} height={18} />
                </Button>
              </Dialog.Close>
            </div>


            <ScrollArea className="min-h-0 flex-1" viewportClassName="h-full px-5 py-4 font-sans">
              <WhmServerFormFields
                form={editForm}
                setForm={setEditForm}
                disabled={savingEdit}
                mode="update"
                existingServer={selectedServer}
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

            </ScrollArea>

            <div className="mt-auto flex items-center justify-end gap-2 border-t border-border px-5 py-4">
              <Dialog.Close asChild>
                <Button disabled={savingEdit} size="sm">
                  Cancel
                </Button>
              </Dialog.Close>
              <Button disabled={savingEdit} onClick={() => void updateServer()} size="sm" variant="default">
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
              "border-l border-border bg-background shadow-md",
              "transition-transform duration-200 ease-out",
              "data-[state=open]:translate-x-0 data-[state=closed]:translate-x-full",
              "outline-none",
            ].join(" ")}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              openerRef.current?.focus();
            }}
          >
            <Dialog.Title className="sr-only">Manage WHM server</Dialog.Title>
            <Dialog.Description className="sr-only">
              Review server details, edit stored credentials, validate the connection, or delete the server.
            </Dialog.Description>

            <div className="flex h-full flex-col">
              <div className="flex items-start justify-between gap-3 border-b border-border bg-card px-4 py-4">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-foreground">{selectedServer?.name ?? "WHM server"}</div>
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
                className="flex-1 min-h-0 font-sans"
                horizontalScrollbar
                viewportClassName="h-full p-4"
              >
                <div className="rounded-xl border border-border bg-card px-4 py-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-wide text-muted">Server details</div>
                    </div>
                    <Button disabled={!selectedServer || deleteBusyId === selectedServer.id} onClick={openEdit} size="sm">
                      Edit server
                    </Button>
                  </div>
                  <dl className="mt-3 grid gap-3 text-sm">
                    <div>
                      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">Name</dt>
                      <dd className="mt-1 text-foreground">{selectedServer?.name ?? "-"}</dd>
                    </div>
                    <div>
                      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">Base URL</dt>
                      <dd className="mt-1 break-all text-foreground">{selectedServer?.base_url ?? "-"}</dd>
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <div>
                        <dt className="text-xs font-semibold uppercase tracking-wide text-muted">API user</dt>
                        <dd className="mt-1 text-foreground">{selectedServer?.api_username ?? "-"}</dd>
                      </div>
                      <div>
                        <dt className="text-xs font-semibold uppercase tracking-wide text-muted">SSL</dt>
                        <dd className="mt-1 text-foreground">{selectedServer?.verify_ssl ? "Verify enabled" : "Verification off"}</dd>
                      </div>
                    </div>
                    <div>
                      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">Updated</dt>
                      <dd className="mt-1 text-foreground">{formatTimestamp(selectedServer?.updated_at)}</dd>
                    </div>
                  </dl>
                </div>

                <div className="mt-4 rounded-xl border border-border bg-card px-4 py-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-wide text-muted">SSH access</div>
                      <div className="mt-2 flex items-center gap-2">
                        <span className={["status-badge", sshStatus.className].join(" ")}>{sshStatus.label}</span>
                        {selectedServer?.ssh_host_key_fingerprint ? (
                          <span className="status-badge status-badge-success">Host key pinned</span>
                        ) : (
                          <span className="status-badge">Fingerprint missing</span>
                        )}
                      </div>
                    </div>
                  </div>
                  <dl className="mt-3 grid gap-3 text-sm">
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <div>
                        <dt className="text-xs font-semibold uppercase tracking-wide text-muted">SSH user</dt>
                        <dd className="mt-1 text-foreground">{selectedServer?.ssh_username || "root (default)"}</dd>
                      </div>
                      <div>
                        <dt className="text-xs font-semibold uppercase tracking-wide text-muted">SSH port</dt>
                        <dd className="mt-1 text-foreground">{selectedServer?.ssh_port ?? 22}</dd>
                      </div>
                    </div>
                    <div>
                      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">Authentication</dt>
                      <dd className="mt-1 text-foreground">{getSshAuthLabel(selectedServer)}</dd>
                    </div>
                    <div>
                      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">Host key fingerprint</dt>
                      <dd className="mt-1 break-all font-mono text-xs text-foreground">
                        {selectedServer?.ssh_host_key_fingerprint ?? "Not validated yet"}
                      </dd>
                    </div>
                  </dl>
                </div>

                <div className="mt-4 rounded-xl border border-border bg-card px-4 py-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold uppercase tracking-wide text-muted">Latest validation</div>
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
                            : "Validate checks the WHM API token path, then SSH (if configured) for CSF and other SSH-backed tools, and refreshes the pinned SSH host key fingerprint."}
                        </p>
                    </div>
                    <Button
                      className="shrink-0"
                      disabled={!selectedServer || validateBusyId === selectedServer.id || deleteBusyId === selectedServer.id}
                      onClick={() => selectedServer && void validateServer(selectedServer.id)}
                      size="sm"
                      variant="default"
                    >
                      {selectedServer && validateBusyId === selectedServer.id ? "Validating..." : "Validate"}
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

                <div className="danger-zone mt-6">
                  <div className="danger-zone-label text-xs font-semibold uppercase tracking-wide">Danger zone</div>
                  <p className="danger-zone-copy mt-1 text-sm">
                    Delete this server configuration from NOA. Stored validation state for this session is removed too.
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
              </ScrollArea>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
