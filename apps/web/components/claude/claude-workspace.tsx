"use client";

import { useCallback, useEffect, useState } from "react";

import * as Dialog from "@radix-ui/react-dialog";

import { ClaudeThread } from "@/components/claude/claude-thread";
import { ClaudeThreadList } from "@/components/claude/claude-thread-list";
import { RequestApprovalToolUI } from "@/components/claude/request-approval-tool-ui";

export function ClaudeWorkspace() {
  const [open, setOpen] = useState(false);
  const [desktopSidebarOpen, setDesktopSidebarOpen] = useState(true);

  const openSidebar = useCallback(() => {
    setDesktopSidebarOpen(true);
    if (window.matchMedia("(min-width: 768px)").matches) return;
    setOpen(true);
  }, []);
  const closeSidebar = useCallback(() => setOpen(false), []);
  const closeDesktopSidebar = useCallback(() => setDesktopSidebarOpen(false), []);

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
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <section className="relative h-dvh w-full overflow-hidden bg-bg">
        <RequestApprovalToolUI />

        <div
          className={[
            "grid h-full min-h-0 grid-cols-1",
            desktopSidebarOpen ? "md:grid-cols-[18rem_minmax(0,1fr)]" : "md:grid-cols-1",
          ].join(" ")}
        >
          {desktopSidebarOpen ? (
            <aside className="hidden h-full min-h-0 border-[#00000010] border-r md:block dark:border-[#6c6a6040]">
              <ClaudeThreadList onCloseSidebar={closeDesktopSidebar} />
            </aside>
          ) : null}

          <div className="h-full min-h-0 min-w-0">
            <ClaudeThread
              onOpenSidebar={openSidebar}
              showOpenSidebarButtonOnDesktop={!desktopSidebarOpen}
            />
          </div>
        </div>

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
              <ClaudeThreadList onSelectThread={closeSidebar} onCloseSidebar={closeSidebar} />
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </section>
    </Dialog.Root>
  );
}
