"use client";

import { useCallback, useState } from "react";

import { Cross2Icon } from "@radix-ui/react-icons";

import { ClaudeThread } from "@/components/claude/claude-thread";
import { ClaudeThreadList } from "@/components/claude/claude-thread-list";
import { RequestApprovalToolUI } from "@/components/claude/request-approval-tool-ui";

export function ClaudeWorkspace() {
  const [open, setOpen] = useState(false);

  const openSidebar = useCallback(() => setOpen(true), []);
  const closeSidebar = useCallback(() => setOpen(false), []);

  return (
    <section className="relative h-[calc(100dvh-2rem)] min-h-[640px] overflow-hidden rounded-2xl border border-[#00000010] bg-[#F5F5F0] shadow-[0_0.5rem_2rem_rgba(0,0,0,0.06)] dark:border-[#6c6a6040] dark:bg-[#2b2a27]">
      <RequestApprovalToolUI />

      <div className="grid h-full grid-cols-1 md:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="hidden h-full border-[#00000010] border-r md:block dark:border-[#6c6a6040]">
          <ClaudeThreadList />
        </aside>

        <div className="h-full min-w-0">
          <ClaudeThread onOpenSidebar={openSidebar} />
        </div>
      </div>

      <div
        className={[
          "md:hidden",
          "pointer-events-none fixed inset-0 z-50",
          open ? "pointer-events-auto" : "",
        ].join(" ")}
        aria-hidden={!open}
      >
        <div
          className={[
            "absolute inset-0 bg-black/30 transition-opacity",
            open ? "opacity-100" : "opacity-0",
          ].join(" ")}
          onClick={closeSidebar}
        />

        <div
          className={[
            "absolute inset-y-0 left-0 w-[86vw] max-w-[360px]",
            "bg-[#F5F5F0] shadow-[0_1rem_3rem_rgba(0,0,0,0.22)]",
            "transition-transform duration-200 ease-out",
            open ? "translate-x-0" : "-translate-x-full",
            "dark:bg-[#2b2a27]",
          ].join(" ")}
        >
          <div className="flex h-12 items-center justify-between border-[#00000010] border-b px-3 dark:border-[#6c6a6040]">
            <div className="text-sm font-medium text-[#1a1a18] dark:text-[#eee]">Chats</div>
            <button
              type="button"
              onClick={closeSidebar}
              className="flex h-9 w-9 items-center justify-center rounded-lg border border-[#00000015] bg-white/70 text-[#6b6a68] shadow-sm backdrop-blur-sm transition hover:bg-white hover:text-[#1a1a18] active:scale-[0.98] dark:border-[#6c6a6040] dark:bg-[#1f1e1b]/70 dark:text-[#9a9893] dark:hover:bg-[#1f1e1b] dark:hover:text-[#eee]"
              aria-label="Close sidebar"
            >
              <Cross2Icon width={18} height={18} />
            </button>
          </div>

          <div className="h-[calc(100%-3rem)]">
            <ClaudeThreadList onSelectThread={closeSidebar} />
          </div>
        </div>
      </div>
    </section>
  );
}
