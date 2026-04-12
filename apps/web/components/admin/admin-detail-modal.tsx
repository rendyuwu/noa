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
  headerActions?: ReactNode;
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
  headerActions,
}: AdminDetailModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          "flex max-h-[calc(100svh-2rem)] flex-col gap-0 overflow-hidden rounded-[32px] border border-border/80 bg-card/80 p-0 shadow-xl shadow-amber-950/5 backdrop-blur",
          sizeClasses[size],
        )}
      >
        <DialogHeader className="shrink-0 border-b border-border/70 bg-background/90 px-6 pt-6 pb-4 pr-14">
          <DialogTitle className="font-serif text-[1.5rem] tracking-[-0.02em]">{title}</DialogTitle>
          {subtitle && <DialogDescription>{subtitle}</DialogDescription>}
          {headerActions ? <div className="flex flex-wrap items-center gap-2 pt-2">{headerActions}</div> : null}
        </DialogHeader>
        <ScrollArea className="min-h-0 flex-1 overscroll-contain">
          <div className="px-6 py-4">{children}</div>
        </ScrollArea>
        {footer && (
          <div className="shrink-0 border-t border-border/70 bg-background/90 px-6 py-4">
            {footer}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
