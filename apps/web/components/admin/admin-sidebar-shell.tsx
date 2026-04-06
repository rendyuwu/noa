"use client";

import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { HamburgerMenuIcon } from "@radix-ui/react-icons";

import { ClaudeThreadList } from "@/components/assistant/claude-thread-list";
import { ScrollArea } from "@/components/lib/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";

type DesktopSidebarMode = "expanded" | "collapsed";

const DESKTOP_SIDEBAR_MODE_STORAGE_KEY = "noa.sidebar.mode.v1";

export function AdminSidebarShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [desktopSidebarMode, setDesktopSidebarMode] = useState<DesktopSidebarMode>("expanded");

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const stored = window.localStorage.getItem(DESKTOP_SIDEBAR_MODE_STORAGE_KEY);
      if (stored === "expanded" || stored === "collapsed") {
        setDesktopSidebarMode(stored);
      }
    } catch {}
  }, []);

  const setDesktopSidebarModePersisted = useCallback((mode: DesktopSidebarMode) => {
    setDesktopSidebarMode(mode);
    try {
      window.localStorage.setItem(DESKTOP_SIDEBAR_MODE_STORAGE_KEY, mode);
    } catch {}
  }, []);

  const expandDesktopSidebar = useCallback(
    () => setDesktopSidebarModePersisted("expanded"),
    [setDesktopSidebarModePersisted],
  );
  const collapseDesktopSidebar = useCallback(
    () => setDesktopSidebarModePersisted("collapsed"),
    [setDesktopSidebarModePersisted],
  );

  const openSidebar = useCallback(() => {
    if (window.matchMedia("(min-width: 768px)").matches) {
      expandDesktopSidebar();
      return;
    }

    setOpen(true);
  }, [expandDesktopSidebar]);

  const closeSidebar = useCallback(() => setOpen(false), []);

  const selectThread = useCallback(() => {
    setOpen(false);
    router.push("/assistant");
  }, [router]);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(min-width: 768px)");

    const closeOnDesktop = (event: MediaQueryList | MediaQueryListEvent) => {
      if (event.matches) setOpen(false);
    };

    closeOnDesktop(mediaQuery);

    if (mediaQuery.addEventListener) {
      mediaQuery.addEventListener("change", closeOnDesktop);
      return () => mediaQuery.removeEventListener("change", closeOnDesktop);
    }

    mediaQuery.addListener(closeOnDesktop);
    return () => mediaQuery.removeListener(closeOnDesktop);
  }, []);

  return (
    <div
      className={[
        "grid h-dvh min-h-0 w-full grid-cols-1 overflow-hidden bg-background transition-[grid-template-columns] duration-200 ease-out motion-reduce:transition-none",
        desktopSidebarMode === "expanded"
          ? "md:grid-cols-[18rem_minmax(0,1fr)]"
          : "md:grid-cols-[3rem_minmax(0,1fr)]",
      ].join(" ")}
    >
      <aside className="hidden h-full min-h-0 border-border border-r md:block">
        <ClaudeThreadList
          variant={desktopSidebarMode}
          onCollapseSidebar={collapseDesktopSidebar}
          onExpandSidebar={expandDesktopSidebar}
          onSelectThread={selectThread}
        />
      </aside>

      <div className="relative h-full min-h-0 min-w-0">
        <div className="absolute top-3 left-3 z-10 flex items-center gap-2 md:hidden">
          <button
            type="button"
            onClick={openSidebar}
            className="flex h-9 items-center gap-2 rounded-lg border border-border bg-card/70 px-3 font-sans text-sm text-muted-foreground shadow-sm backdrop-blur-sm transition hover:bg-card hover:text-foreground active:scale-[0.98]"
          >
            <HamburgerMenuIcon width={16} height={16} />
            Open sidebar
          </button>
        </div>

        <ScrollArea className="h-full min-h-0" horizontalScrollbar viewportClassName="h-full">
          {children}
        </ScrollArea>
      </div>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left" className="w-[18rem] max-w-[86vw] p-0 md:hidden">
          <SheetTitle className="sr-only">Chats</SheetTitle>
          <SheetDescription className="sr-only">
            Browse recent conversations and start a new chat.
          </SheetDescription>
          <div className="h-full">
            <ClaudeThreadList onSelectThread={selectThread} onCloseSidebar={closeSidebar} />
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
