"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";

import { useAssistantApi, useAssistantState } from "@assistant-ui/react";

import { ClaudeThread } from "@/components/assistant/claude-thread";
import { ClaudeThreadList } from "@/components/assistant/claude-thread-list";
import { RequestApprovalToolUI } from "@/components/assistant/request-approval-tool-ui";
import { WorkflowReceiptToolUI } from "@/components/assistant/workflow-receipt-tool-ui";
import { WorkflowTodoToolUI } from "@/components/assistant/workflow-todo-tool-ui";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";

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
    <Sheet open={open} onOpenChange={setOpen}>
      <section className="relative h-dvh w-full overflow-hidden bg-background">
        <RequestApprovalToolUI />
        <WorkflowTodoToolUI />
        <WorkflowReceiptToolUI />

        <div
          className={[
            "grid h-full min-h-0 grid-cols-1 transition-[grid-template-columns] duration-200 ease-out motion-reduce:transition-none",
            desktopSidebarMode === "expanded"
              ? "md:grid-cols-[20rem_minmax(0,1fr)]"
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
                <div className="mx-auto max-w-[60rem] px-4 pt-4 md:pt-5">
                  <div className="overflow-hidden rounded-[28px] border border-border/80 bg-card/90 shadow-xl shadow-amber-950/5 backdrop-blur">
                    <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
                      <div className="min-w-0 space-y-1">
                        <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-muted-foreground">
                          Editorial routing
                        </p>
                        <p className="font-sans text-sm text-foreground">{routeThreadError}</p>
                      </div>

                      <button
                        type="button"
                        onClick={() => router.push("/assistant", { scroll: false })}
                        className="inline-flex items-center justify-center rounded-full bg-foreground px-4 py-2 text-sm font-semibold text-background shadow-sm transition hover:bg-foreground/90 active:scale-[0.99]"
                      >
                        New chat
                      </button>
                    </div>
                  </div>
                </div>
              ) : null}
              <ClaudeThread
                onOpenSidebarAction={openSidebar}
                forceHydrationSkeleton={forceHydrationSkeleton}
              />
            </div>
          </div>

        <SheetContent side="left" className="w-[20rem] max-w-[86vw] p-0 md:hidden">
          <SheetTitle className="sr-only">Chats</SheetTitle>
          <SheetDescription className="sr-only">
            Browse recent conversations and start a new chat.
          </SheetDescription>
          <div className="h-full">
            <ClaudeThreadList onSelectThread={closeSidebar} onCloseSidebar={closeSidebar} />
          </div>
        </SheetContent>
      </section>
    </Sheet>
  );
}
