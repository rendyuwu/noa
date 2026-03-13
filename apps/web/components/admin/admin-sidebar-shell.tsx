"use client";

import type { ReactNode } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import * as Dialog from "@radix-ui/react-dialog";
import { HamburgerMenuIcon } from "@radix-ui/react-icons";

import { ClaudeThreadList } from "@/components/claude/claude-thread-list";

export function AdminSidebarShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(false);
  const desktopSidebarDismissedRef = useRef(false);

  const openSidebar = useCallback(() => {
    desktopSidebarDismissedRef.current = false;
    setDesktopSidebarOpen(true);
    if (window.matchMedia("(min-width: 768px)").matches) return;
    setOpen(true);
  }, []);

  const closeSidebar = useCallback(() => setOpen(false), []);
  const closeDesktopSidebar = useCallback(() => {
    desktopSidebarDismissedRef.current = true;
    setDesktopSidebarOpen(false);
  }, []);

  const selectThread = useCallback(() => {
    setOpen(false);
    router.push("/assistant");
  }, [router]);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(min-width: 768px)");

    const closeOnDesktop = (event: MediaQueryList | MediaQueryListEvent) => {
      if (!event.matches) return;

      setOpen(false);
      if (desktopSidebarDismissedRef.current) return;
      setDesktopSidebarOpen(true);
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
        "grid h-dvh min-h-0 w-full grid-cols-1 overflow-hidden bg-bg",
        desktopSidebarOpen ? "md:grid-cols-[18rem_minmax(0,1fr)]" : "md:grid-cols-1",
      ].join(" ")}
    >
      {desktopSidebarOpen ? (
        <aside className="hidden h-full min-h-0 border-border border-r md:block">
          <ClaudeThreadList onCloseSidebar={closeDesktopSidebar} onSelectThread={selectThread} />
        </aside>
      ) : null}

      <div className="relative h-full min-h-0 min-w-0">
        <div
          className={[
            "absolute top-3 left-3 z-10 flex items-center gap-2",
            desktopSidebarOpen ? "md:hidden" : "",
          ].join(" ")}
        >
          <button
            type="button"
            onClick={openSidebar}
            className="flex h-9 items-center gap-2 rounded-lg border border-border bg-surface/70 px-3 font-ui text-sm text-muted shadow-sm backdrop-blur-sm transition hover:bg-surface hover:text-text active:scale-[0.98]"
          >
            <HamburgerMenuIcon width={16} height={16} />
            Open sidebar
          </button>
        </div>

        <div className="h-full min-h-0 overflow-auto">{children}</div>
      </div>

      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/30 opacity-0 transition-opacity data-[state=open]:opacity-100 data-[state=closed]:opacity-0 md:hidden" />

          <Dialog.Content
            className={[
              "fixed inset-y-0 left-0 z-50 w-[18rem] max-w-[86vw]",
              "bg-bg shadow-[0_1rem_3rem_rgba(0,0,0,0.22)]",
              "transition-transform duration-200 ease-out",
              "data-[state=open]:translate-x-0 data-[state=closed]:-translate-x-full",
              "outline-none",
              "md:hidden",
            ].join(" ")}
          >
            <Dialog.Title className="sr-only">Chats</Dialog.Title>
            <Dialog.Description className="sr-only">
              Browse recent conversations and start a new chat.
            </Dialog.Description>
            <div className="h-full">
              <ClaudeThreadList onSelectThread={selectThread} onCloseSidebar={closeSidebar} />
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
