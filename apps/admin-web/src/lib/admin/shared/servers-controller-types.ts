// The public type surface of the shared admin servers controller (#131 finding
// #15). Split from use-servers-controller.ts to keep that module within the
// repo's file-size budget; the hook re-exports MutationResult and
// ServersController so the per-vertical wrappers still import everything from
// one place.

export type MutationResult =
  | { ok: true; current: boolean }
  | { ok: false; message: string; current: boolean }

// The minimal shape the controller needs from a server (sorted by name) and from
// a validation response (its ok flag + a fallback message). Each vertical passes
// its full domain types, which structurally satisfy these bounds.
export type ServerLike = { id: string; name: string }
export type ValidateResultLike = { ok: boolean; message?: string }

export type ServersControllerMessages = {
  load: string
  create: string
  update: string
  delete: string
  validate: string
}

export type ServersControllerApi<TServer extends ServerLike, TValidate extends ValidateResultLike> = {
  fetchServers: () => Promise<TServer[]>
  createServer: (body: Record<string, unknown>) => Promise<TServer>
  updateServer: (serverId: string, body: Record<string, unknown>) => Promise<TServer>
  deleteServer: (serverId: string) => Promise<void>
  validateServer: (serverId: string) => Promise<TValidate>
  messages: ServersControllerMessages
}

export type ServersController<TServer extends ServerLike, TValidate extends ValidateResultLike> = {
  servers: TServer[]
  loading: boolean
  loadError: string | null
  reload: () => Promise<void>
  selectedServer: TServer | null
  selectServer: (serverId: string | null) => void
  validateResultById: Record<string, TValidate>
  validateBusyId: string | null
  deleteBusyId: string | null
  createServer: (body: Record<string, unknown>) => Promise<MutationResult>
  updateServer: (serverId: string, body: Record<string, unknown>) => Promise<MutationResult>
  deleteServer: (serverId: string) => Promise<MutationResult>
  validateServer: (serverId: string) => Promise<MutationResult>
}
