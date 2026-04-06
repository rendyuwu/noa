"use client";

import { forwardRef } from "react";
import type { ComponentPropsWithoutRef, ElementRef } from "react";

import * as ScrollAreaPrimitive from "@radix-ui/react-scroll-area";

import { cn } from "@/lib/utils";

export type ScrollAreaProps = ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.Root> & {
  viewportClassName?: string;
  verticalScrollbar?: boolean;
  horizontalScrollbar?: boolean;
};

export const ScrollArea = forwardRef<ElementRef<typeof ScrollAreaPrimitive.Root>, ScrollAreaProps>(
  function ScrollArea(
    {
      className,
      viewportClassName,
      verticalScrollbar = true,
      horizontalScrollbar = false,
      children,
      ...props
    },
    ref,
  ) {
    return (
      <ScrollAreaPrimitive.Root
        ref={ref}
        data-slot="scroll-area"
        className={cn("relative overflow-hidden", className)}
        {...props}
      >
        <ScrollAreaPrimitive.Viewport
          data-slot="scroll-area-viewport"
          className={cn("size-full rounded-[inherit]", viewportClassName)}
        >
          {children}
        </ScrollAreaPrimitive.Viewport>
        {verticalScrollbar ? <ScrollBar /> : null}
        {horizontalScrollbar ? <ScrollBar orientation="horizontal" /> : null}
        <ScrollAreaPrimitive.Corner data-slot="scroll-area-corner" className="bg-transparent" />
      </ScrollAreaPrimitive.Root>
    );
  },
);

export type ScrollBarProps = ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.Scrollbar>;

export const ScrollBar = forwardRef<ElementRef<typeof ScrollAreaPrimitive.Scrollbar>, ScrollBarProps>(
  function ScrollBar({ className, orientation = "vertical", ...props }, ref) {
    return (
      <ScrollAreaPrimitive.Scrollbar
        ref={ref}
        data-slot="scroll-area-scrollbar"
        orientation={orientation}
        className={cn(
          "flex touch-none select-none p-px transition-colors",
          orientation === "vertical" && "h-full w-2.5 border-l border-l-transparent",
          orientation === "horizontal" && "h-2.5 flex-col border-t border-t-transparent",
          className,
        )}
        {...props}
      >
        <ScrollAreaPrimitive.Thumb
          data-slot="scroll-area-thumb"
          className="relative flex-1 rounded-full bg-border/80 transition-colors hover:bg-border"
        />
      </ScrollAreaPrimitive.Scrollbar>
    );
  },
);
