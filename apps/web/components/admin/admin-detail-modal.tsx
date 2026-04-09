"use client";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type AdminDetailModalProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  subtitle?: string;
  size?: "sm" | "md" | "lg";
  children: ReactNode;
  footer?: ReactNode;
};

const sizeClasses = {
  sm: "sm:max-w-md",
  md: "sm:max-w-lg",
  lg: "sm:max-w-2xl",
};

export function AdminDetailModal({
  open,
  onOpenChange,
  title,
  subtitle,
  size = "md",
  children,
  footer,
}: AdminDetailModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn("max-h-[85vh] flex flex-col gap-0 p-0", sizeClasses[size])}>
        <DialogHeader className="px-6 pt-6 pb-4 border-b border-border shrink-0">
          <DialogTitle>{title}</DialogTitle>
          {subtitle && <DialogDescription>{subtitle}</DialogDescription>}
        </DialogHeader>
        <ScrollArea className="flex-1 min-h-0">
          <div className="px-6 py-4">{children}</div>
        </ScrollArea>
        {footer && (
          <div className="px-6 py-4 border-t border-border shrink-0">
            {footer}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
