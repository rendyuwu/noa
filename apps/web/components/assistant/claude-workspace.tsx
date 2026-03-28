"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";

import * as Dialog from "@radix-ui/react-dialog";

import { useAssistantApi, useAssistantState } from "@assistant-ui/react";

import { ClaudeThread } from "@/components/assistant/claude-thread";
import { ClaudeThreadList } from "@/components/assistant/claude-thread-list";
import { RequestApprovalToolUI } from "@/components/assistant/request-approval-tool-ui";
import { WorkflowReceiptToolUI } from "@/components/assistant/workflow-receipt-tool-ui";
import { WorkflowTodoToolUI } from "@/components/assistant/workflow-todo-tool-ui";

import { getActiveThreadListItem } from "@/components/lib/assistant-thread-state";
import { ApiError } from "@/components/lib/fetch-helper";

type DesktopSidebarMode = "expanded" | "collapsed";

const DESKTOP_SIDEBAR_MODE_STORAGE_KEY = "noa.sidebar.mode.v1";

export function ClaudeWorkspace() {
  const api = useAssistantApi();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const params = useParams();

  const activeRemoteId = useAssistantState(
    ({ threads }: any) => getActiveThreadListItem(threads)?.remoteId ?? null,
  );
  const activeStatus = useAssistantState(
    ({ threads }: any) => getActiveThreadListItem(threads)?.status ?? "new",
  );

  const routeThreadId = (() => {
    const value = (params as any).threadId;
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return typeof value[0] === "string" ? value[0] : null;
    return null;
  })();
  const legacyThreadId = searchParams.get("threadId");

  const [open, setOpen] = useState(false);
  const [desktopSidebarMode, setDesktopSidebarMode] = useState<DesktopSidebarMode>("expanded");
  const lastRoutedKey = useRef<string | null>(null);
  const [routeThreadError, setRouteThreadError] = useState<string | null>(null);

  const forceHydrationSkeleton =
    Boolean(routeThreadId) &&
    !routeThreadError &&
    activeRemoteId !== routeThreadId;

  const isUuidLike = useCallback((value: string) => {
    return /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(
      value,
    );
  }, []);

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

  useEffect(() => {
    if (!routeThreadId && legacyThreadId && pathname === "/assistant") {
      const next = `/assistant/${legacyThreadId}`;
      router.replace(next, { scroll: false });
      return;
    }

    const key = routeThreadId ? `thread:${routeThreadId}` : "new";
    if (lastRoutedKey.current === key) return;
    lastRoutedKey.current = key;

    const alreadyOnThread = Boolean(routeThreadId) && activeRemoteId === routeThreadId;
    const alreadyOnDraft = !routeThreadId && !activeRemoteId && activeStatus === "new";

    if (alreadyOnThread || alreadyOnDraft) {
      setRouteThreadError(null);
      return;
    }

    setRouteThreadError(null);

    void (async () => {
      try {
        if (routeThreadId) {
          if (!isUuidLike(routeThreadId)) {
            setRouteThreadError("Invalid chat link.");
            try {
              await api.threads().switchToNewThread();
            } catch {}
            return;
          }

          await api.threads().switchToThread(routeThreadId);
          return;
        }

        await api.threads().switchToNewThread();
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          setRouteThreadError(null);
          router.replace("/assistant", { scroll: false });
          try {
            await api.threads().switchToNewThread();
          } catch {}
          return;
        }

        setRouteThreadError("This chat link is invalid or you don't have access.");
        console.error("Failed to switch to thread", error);
        try {
          await api.threads().switchToNewThread();
        } catch {}
      }
    })();
  }, [
    activeRemoteId,
    activeStatus,
    api,
    isUuidLike,
    legacyThreadId,
    pathname,
    routeThreadId,
    router,
  ]);

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <section className="relative h-dvh w-full overflow-hidden bg-bg">
        <RequestApprovalToolUI />
        <WorkflowTodoToolUI />
        <WorkflowReceiptToolUI />

        <div
          className={[
            "grid h-full min-h-0 grid-cols-1 transition-[grid-template-columns] duration-200 ease-out motion-reduce:transition-none",
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
            />
          </aside>

           <div className="h-full min-h-0 min-w-0">
             {routeThreadError ? (
               <div className="mx-auto max-w-[56rem] px-4 pt-4">
                 <div className="rounded-2xl border border-border bg-surface/70 px-4 py-3 font-ui text-sm text-text shadow-sm">
                   <div className="flex flex-wrap items-center justify-between gap-3">
                     <p>{routeThreadError}</p>
                      <button
                        type="button"
                        onClick={() => router.push("/assistant", { scroll: false })}
                        className="inline-flex items-center justify-center rounded-xl bg-accent px-3 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-accent/90 active:scale-[0.99]"
                      >
                       New chat
                     </button>
                   </div>
                 </div>
               </div>
             ) : null}
              <ClaudeThread
                onOpenSidebar={openSidebar}
                forceHydrationSkeleton={forceHydrationSkeleton}
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
