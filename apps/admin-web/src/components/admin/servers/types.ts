import type { ReactNode } from 'react'
import type { DataTableProps, StatusChipStatus } from '@gio/bigsu-ui'
import type { FieldValues, UseFormReturn } from 'react-hook-form'

import type { BadgeSpec } from '@/lib/admin/shared/server-status'
import type {
  ServerLike,
  ServersController,
  ValidateResultLike,
} from '@/lib/admin/shared/servers-controller-types'

// The contract a server vertical implements to get the whole admin servers UI —
// page, table, detail drawer and create/edit dialog — without writing any of it.
// The WHM, PMG and Proxmox verticals each shipped a private copy of all four;
// what actually differed between them was copy, which columns a vertical shows,
// what its drawer lists, and its form fields. Those differences are this type.
//
// The split into three slices is deliberate: each generic component takes only
// the slice it reads, so the columns builder cannot see the form half and the
// dialog cannot see the table half.

export type FormMode = 'create' | 'update'

// Every admin server row is sortable by name and carries an optional wire
// timestamp for the Updated column.
export type AdminServer = ServerLike & { updated_at?: string }

export type DetailRowSpec = { label: string; value: ReactNode }

export type DetailSection = {
  title: string
  status?: StatusChipStatus
  badges: BadgeSpec[]
  rows: DetailRowSpec[]
}

export type ServerDetailView = {
  /** DrawerDescription — the one address that identifies this server. */
  subtitle: string
  /** Beside the validation StatusChip. */
  badges: BadgeSpec[]
  /** The first <dl>, below the always-present Server ID row. */
  rows: DetailRowSpec[]
  /** Zero or one today: the WHM/PMG "SSH access" block. */
  sections: DetailSection[]
  deleteDescription: string
}

// Table slice.
export type ServerColumnSpec<TServer extends AdminServer> = {
  rowSubtitle: (server: TServer) => string
  /** When set, the Server cell reserves a badge slot beside the name. */
  rowBadge?: (server: TServer) => BadgeSpec | null
  /** Inserted between the Validation and Updated columns, in order. */
  extraColumns: DataTableProps<TServer>['columns']
}

// Form slice.
export type ServerFormSpec<TServer extends AdminServer, TForm extends FieldValues> = {
  /** 'WHM server' — drives every dialog title and toast. */
  noun: string
  formDescription: (mode: FormMode) => string
  formDefaults: (mode: FormMode, existingServer: TServer | null) => TForm
  validateForm: (
    values: TForm,
    mode: FormMode,
    existingServer: TServer | null,
  ) => { field: keyof TForm & string; message: string } | null
  buildPayload: (
    values: TForm,
    mode: FormMode,
    existingServer: TServer | null,
  ) => Record<string, unknown>
  renderFields: (ctx: {
    form: UseFormReturn<TForm>
    mode: FormMode
    existingServer: TServer | null
    busy: boolean
  }) => ReactNode
  /** Proxmox blanks its write-only secret here, whatever the submit outcome. */
  afterSubmit?: (form: UseFormReturn<TForm>) => void
}

export type ServerVertical<
  TServer extends AdminServer,
  TValidate extends ValidateResultLike,
  TForm extends FieldValues,
> = ServerColumnSpec<TServer> &
  ServerFormSpec<TServer, TForm> & {
    useServers: () => ServersController<TServer, TValidate>

    title: string
    description: string
    searchPlaceholder: string
    emptyTitle: string
    emptyDescription: string
    /** Searched in addition to `name` and `id`. */
    searchFields: (server: TServer) => string[]

    detail: (server: TServer, validateResult: TValidate | undefined) => ServerDetailView
  }
