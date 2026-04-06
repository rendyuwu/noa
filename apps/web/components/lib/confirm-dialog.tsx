"use client";

import { useCallback, useMemo, useState } from "react";
import type { ReactNode } from "react";

import * as Dialog from "@radix-ui/react-dialog";

import { Button } from "@/components/lib/button";

type ConfirmDialogVariant = "secondary" | "primary" | "danger";

export type ConfirmDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  confirmLabel: string;
  confirmBusyLabel?: string;
  confirmVariant?: ConfirmDialogVariant;
  cancelLabel?: string;
  busy?: boolean;
  error?: string | null;
  /**
   * When true, close the dialog after a successful confirm.
   *
   * Success is determined by the return value of `onConfirm`:
   * - return `false` to keep the dialog open
   * - any other return value closes when the handler completes
   */
  closeOnConfirm?: boolean;
  onConfirm: () => void | boolean | Promise<void | boolean>;
  children?: ReactNode;
};

export type ConfirmActionProps = Omit<ConfirmDialogProps, "open" | "onOpenChange"> & {
  trigger: (options: { open: () => void; disabled: boolean }) => ReactNode;
};

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  confirmBusyLabel,
  confirmVariant = "danger",
  cancelLabel = "Cancel",
  busy: busyProp,
  error,
  closeOnConfirm = false,
  onConfirm,
  children,
}: ConfirmDialogProps) {
  const [localBusy, setLocalBusy] = useState(false);
  const busy = busyProp ?? localBusy;

  const resolvedBusyLabel = useMemo(() => {
    if (confirmBusyLabel) return confirmBusyLabel;
    if (confirmVariant === "danger") return "Deleting...";
    return "Working...";
  }, [confirmBusyLabel, confirmVariant]);

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (busy) return;
      onOpenChange(nextOpen);
    },
    [busy, onOpenChange],
  );

  const handleConfirm = useCallback(async () => {
    if (busy) return;

    const maybePromise = onConfirm();
    const isPromise = maybePromise && typeof (maybePromise as any).then === "function";

    let outcome: void | boolean = undefined;

    if (isPromise) {
      if (!busyProp) {
        setLocalBusy(true);
      }

      try {
        outcome = await (maybePromise as Promise<void | boolean>);
      } finally {
        if (!busyProp) {
          setLocalBusy(false);
        }
      }
    } else {
      outcome = maybePromise as void | boolean;
    }

    if (closeOnConfirm && outcome !== false) {
      handleOpenChange(false);
    }
  }, [busy, busyProp, closeOnConfirm, handleOpenChange, onConfirm]);

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30 opacity-0 transition-opacity data-[state=open]:opacity-100 data-[state=closed]:opacity-0" />

        <Dialog.Content className="fixed top-1/2 left-1/2 z-50 w-[min(92vw,460px)] -translate-x-1/2 -translate-y-1/2 overflow-hidden rounded-2xl border border-border bg-bg shadow-[0_1.25rem_3rem_rgba(0,0,0,0.22)] outline-none">
          <div className="border-b border-border bg-surface/50 px-5 py-4">
            <Dialog.Title className="text-lg font-semibold text-text">{title}</Dialog.Title>
            {description ? (
              <Dialog.Description className="mt-1 font-ui text-sm text-muted">
                {description}
              </Dialog.Description>
            ) : null}
          </div>

          <div className="px-5 py-4 font-ui">
            {children}
            {error ? (
              <p
                role="alert"
                aria-live="assertive"
                className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
              >
                {error}
              </p>
            ) : null}
          </div>

          <div className="flex items-center justify-end gap-2 border-t border-border bg-surface/40 px-5 py-4">
            <Dialog.Close asChild>
              <Button disabled={busy}>{cancelLabel}</Button>
            </Dialog.Close>
            <Button disabled={busy} onClick={() => void handleConfirm()} variant={confirmVariant}>
              {busy ? resolvedBusyLabel : confirmLabel}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function ConfirmAction({ trigger, busy, ...dialogProps }: ConfirmActionProps) {
  const [open, setOpen] = useState(false);

  const openConfirm = useCallback(() => {
    if (busy) return;
    setOpen(true);
  }, [busy]);

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (busy) return;
      setOpen(nextOpen);
    },
    [busy],
  );

  return (
    <>
      {trigger({ open: openConfirm, disabled: Boolean(busy) })}
      <ConfirmDialog open={open} onOpenChange={handleOpenChange} busy={busy} {...dialogProps} />
    </>
  );
}
