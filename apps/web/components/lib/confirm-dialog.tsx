"use client";

import { useCallback, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { InlineAlert } from "@/components/noa/inline-alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

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
  closeOnConfirm?: boolean;
  onConfirm: () => void | boolean | Promise<void | boolean>;
  children?: ReactNode;
};

export type ConfirmActionProps = Omit<ConfirmDialogProps, "open" | "onOpenChange"> & {
  trigger: (options: { open: () => void; disabled: boolean }) => ReactNode;
};

function mapVariant(variant: ConfirmDialogVariant) {
  if (variant === "primary") return "default" as const;
  if (variant === "secondary") return "outline" as const;
  return "destructive" as const;
}

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
    const isPromise = maybePromise && typeof (maybePromise as Promise<unknown>).then === "function";

    let outcome: void | boolean = undefined;

    if (isPromise) {
      if (!busyProp) setLocalBusy(true);
      try {
        outcome = await (maybePromise as Promise<void | boolean>);
      } finally {
        if (!busyProp) setLocalBusy(false);
      }
    } else {
      outcome = maybePromise as void | boolean;
    }

    if (closeOnConfirm && outcome !== false) {
      handleOpenChange(false);
    }
  }, [busy, busyProp, closeOnConfirm, handleOpenChange, onConfirm]);

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[460px]">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>

        <div className="font-sans">
          {children}
          {error ? (
            <InlineAlert variant="destructive" className="mt-3" role="alert" aria-live="assertive">
              {error}
            </InlineAlert>
          ) : null}
        </div>

        <DialogFooter>
          <Button disabled={busy} variant="outline" onClick={() => handleOpenChange(false)}>
            {cancelLabel}
          </Button>
          <Button disabled={busy} onClick={() => void handleConfirm()} variant={mapVariant(confirmVariant)}>
            {busy ? resolvedBusyLabel : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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
