import { render, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

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

  it("uses the softer scrollbar tone", async () => {
    render(
      <ScrollArea className="h-24 w-24" type="always">
        <div className="h-64 w-64">Editorial scroll</div>
      </ScrollArea>,
    );

    await waitFor(() => {
      expect(document.querySelector('[data-slot="scroll-area-thumb"]')).not.toBeNull();
    });

    const thumb = document.querySelector('[data-slot="scroll-area-thumb"]');

    expect(thumb).toHaveClass("bg-border/70");
    expect(thumb).toHaveClass("hover:bg-border/80");
  });
});
