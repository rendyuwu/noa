import * as React from "react";

import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "./dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "./dropdown-menu";
import { ScrollArea } from "./scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "./sheet";
import { ScrollArea as LibScrollArea } from "../lib/scroll-area";

const { uiScrollAreaCalls, libScrollAreaCalls } = vi.hoisted(() => ({
  uiScrollAreaCalls: [] as Array<{ scrollbar?: Record<string, unknown>; thumb?: Record<string, unknown> }>,
  libScrollAreaCalls: [] as Array<{ scrollbar?: Record<string, unknown>; thumb?: Record<string, unknown> }>,
}));

vi.mock("radix-ui", async () => {
  const actual = await vi.importActual<typeof import("radix-ui")>("radix-ui");

  return {
    ...actual,
    ScrollArea: {
      ...actual.ScrollArea,
      ScrollAreaScrollbar: ({ children, ...props }: any) => {
        uiScrollAreaCalls.push({ scrollbar: props });
        return <div data-slot="scroll-area-scrollbar">{children}</div>;
      },
      ScrollAreaThumb: (props: any) => {
        uiScrollAreaCalls[uiScrollAreaCalls.length - 1] ??= {};
        uiScrollAreaCalls[uiScrollAreaCalls.length - 1].thumb = props;
        return <div data-slot="scroll-area-thumb" />;
      },
    },
  };
});

vi.mock("@radix-ui/react-scroll-area", async () => {
  const actual = await vi.importActual<typeof import("@radix-ui/react-scroll-area")>("@radix-ui/react-scroll-area");

  return {
    ...actual,
    Scrollbar: ({ children, ...props }: any) => {
      libScrollAreaCalls.push({ scrollbar: props });
      return <div data-slot="scroll-area-scrollbar">{children}</div>;
    },
    Thumb: (props: any) => {
      libScrollAreaCalls[libScrollAreaCalls.length - 1] ??= {};
      libScrollAreaCalls[libScrollAreaCalls.length - 1].thumb = props;
      return <div data-slot="scroll-area-thumb" />;
    },
  };
});

describe("editorial overlays", () => {
  it("uses the warm dialog surface", () => {
    render(
      <Dialog open>
        <DialogContent>
          <DialogTitle>Editorial dialog</DialogTitle>
          <DialogDescription>Dialog body</DialogDescription>
        </DialogContent>
      </Dialog>,
    );

    const dialogContent = document.querySelector('[data-slot="dialog-content"]');

    expect(dialogContent).not.toBeNull();
    expect(dialogContent).toHaveClass("rounded-2xl");
    expect(dialogContent).toHaveClass("bg-card/90");
    expect(dialogContent).toHaveClass("border-border/70");
  });

  it("uses the card-like sheet surface", () => {
    render(
      <Sheet open>
        <SheetContent>
          <SheetTitle>Editorial sheet</SheetTitle>
          <SheetDescription>Sheet body</SheetDescription>
        </SheetContent>
      </Sheet>,
    );

    const sheetContent = document.querySelector('[data-slot="sheet-content"]');

    expect(sheetContent).not.toBeNull();
    expect(sheetContent).toHaveClass("bg-card/95");
    expect(sheetContent).toHaveClass("rounded-[28px]");
    expect(sheetContent).toHaveClass("shadow-[0_24px_60px_-32px_rgba(15,23,42,0.3)]");
  });

  it("uses the rounded dropdown menu surface", () => {
    render(
      <DropdownMenu open>
        <DropdownMenuTrigger>Open</DropdownMenuTrigger>
        <DropdownMenuContent>Editorial menu</DropdownMenuContent>
      </DropdownMenu>,
    );

    const menuContent = document.querySelector('[data-slot="dropdown-menu-content"]');

    expect(menuContent).not.toBeNull();
    expect(menuContent).toHaveClass("rounded-2xl");
    expect(menuContent).toHaveClass("bg-popover/95");
    expect(menuContent).toHaveClass("shadow-[0_16px_40px_-24px_rgba(15,23,42,0.25)]");
  });

  it("uses the softer scrollbar tone in both implementations", () => {
    render(
      <>
        <ScrollArea className="h-24 w-24">
          <div className="h-64 w-64">Editorial scroll</div>
        </ScrollArea>
        <LibScrollArea className="h-24 w-24">
          <div className="h-64 w-64">Editorial scroll</div>
        </LibScrollArea>
      </>,
    );

    expect(uiScrollAreaCalls[0].scrollbar).not.toHaveProperty("forceMount");
    expect(uiScrollAreaCalls[0].thumb).not.toHaveProperty("forceMount");
    expect(libScrollAreaCalls[0].scrollbar).not.toHaveProperty("forceMount");
    expect(libScrollAreaCalls[0].thumb).not.toHaveProperty("forceMount");

    expect(document.querySelectorAll('[data-slot="scroll-area-scrollbar"]')).toHaveLength(2);
    expect(document.querySelectorAll('[data-slot="scroll-area-thumb"]')).toHaveLength(2);
  });
});
