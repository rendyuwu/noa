"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import type { FC, ReactNode } from "react";

import {
  ThreadListItemByIndexProvider,
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  useAssistantApi,
  useAssistantRuntime,
  useAssistantState,
} from "@assistant-ui/react";
import {
  ColumnsIcon,
  GearIcon,
  MagnifyingGlassIcon,
  PlusIcon,
  TrashIcon,
} from "@radix-ui/react-icons";

import { formatClaudeGreetingName } from "@/components/assistant/claude-greeting";
import { AccountMenu } from "@/components/noa/account-menu";
import { DisabledNavItem, NavLinkItem } from "@/components/noa/nav-link-item";
import { getActiveThreadListItem } from "@/components/lib/assistant-thread-state";
import { clearAuth, getAuthUser } from "@/components/lib/auth-store";
import { ConfirmAction } from "@/components/lib/confirm-dialog";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";
import { useResetAssistantRuntime } from "@/components/lib/runtime-provider";

const sleep = (durationMs: number) => new Promise<void>((resolve) => window.setTimeout(resolve, durationMs));

function isMissingThreadItemLookupError(error: unknown) {
  return error instanceof Error && error.message.includes("Resource not found for lookup");
}


const ThreadListItem: FC<{
  onSelect?: () => void;
  activeRemoteId?: string | null;
  itemId: string;
  remoteId: string;
  threadTitle?: string | null;
}> = ({
  onSelect,
  activeRemoteId,
  itemId,
  remoteId,
  threadTitle,
}) => {
  const runtime = useAssistantRuntime();
  const resetRuntime = useResetAssistantRuntime();
  const router = useRouter();
  const pathname = usePathname();
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const titleTooltip = threadTitle?.trim();

  return (
    <ThreadListItemPrimitive.Root className="group flex items-center gap-2 rounded-xl border border-transparent px-3 py-2 transition-colors hover:border-sidebar-border/70 hover:bg-sidebar-accent/80 data-[active]:border-sidebar-border data-[active]:bg-sidebar-accent">
      <ThreadListItemPrimitive.Trigger
        onClick={() => {
          onSelect?.();

          const href = `/assistant/${remoteId}`;

          void (async () => {
            try {
              await runtime.threads.getItemById(itemId).switchTo();
            } catch (error) {
              console.error("Failed to switch to selected thread", error);
            }

            if (pathname !== href) {
              router.push(href, { scroll: false });
            }
          })();
        }}
        className="min-w-0 flex-1 rounded-lg px-1 text-left font-sans text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        <span className="block line-clamp-2 text-sm leading-5" title={titleTooltip || remoteId}>
          <ThreadListItemPrimitive.Title fallback="Untitled" />
        </span>
      </ThreadListItemPrimitive.Trigger>

      <ConfirmAction
        title="Delete thread?"
        description="This permanently deletes this thread."
        confirmLabel="Delete thread"
        confirmVariant="danger"
        error={deleteError}
        closeOnConfirm
        onConfirm={async () => {
          setDeleteError(null);

          const deletingThreadId = itemId;
          const deletingRemoteId = remoteId;
          const deletingRoute = `/assistant/${deletingRemoteId}`;
          const isActiveThread =
            activeRemoteId === deletingRemoteId ||
            runtime.threads.getState().mainThreadId === deletingThreadId;
          const isActiveRoute = pathnameRef.current === deletingRoute;

          try {
            if (isActiveThread && runtime.thread.getState().isRunning) {
              runtime.thread.cancelRun();

              for (let attempt = 0; attempt < 20; attempt += 1) {
                if (!runtime.thread.getState().isRunning) {
                  break;
                }

                await new Promise<void>((resolve) => window.setTimeout(resolve, 50));
              }
            }

            if (isActiveThread || isActiveRoute) {
              if (isActiveRoute) {
                router.replace("/assistant", { scroll: false });
              }

              await runtime.threads.switchToNewThread();

              const start = Date.now();
              let switchedAway = false;
              let leftDeletingRoute = false;

              while (Date.now() - start < 2000) {
                const mainThreadId = runtime.threads.getState().mainThreadId;
                const leftDeletingThread = mainThreadId !== deletingThreadId;
                leftDeletingRoute = pathnameRef.current !== deletingRoute;

                if (leftDeletingThread && leftDeletingRoute) {
                  switchedAway = true;
                  break;
                }

                await sleep(25);
              }

              if (!switchedAway) {
                console.warn(
                  "Failed to switch away from deleting thread or leave its route within timeout; continuing with API delete",
                  {
                    deletingThreadId,
                    deletingRemoteId,
                    mainThreadId: runtime.threads.getState().mainThreadId,
                    pathname: pathnameRef.current,
                  },
                );
              }
            }

            const response = await fetchWithAuth(`/threads/${deletingRemoteId}`, { method: "DELETE" });
            if (response.status !== 204 && response.status !== 404 && !response.ok) {
              await jsonOrThrow(response);
            }

            resetRuntime();
            return true;
          } catch (error) {
            if (isMissingThreadItemLookupError(error)) {
              return true;
            }

            setDeleteError(error instanceof Error ? error.message : "Failed to delete thread.");
            return false;
          }
        }}
        trigger={({ open, disabled }) => (
          <button
            type="button"
            className="shrink-0 flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground opacity-0 transition hover:bg-sidebar-accent hover:text-foreground group-hover:opacity-100 group-focus-within:opacity-100 group-data-[active]:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            aria-label="Delete thread"
            disabled={disabled}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              open();
            }}
          >
            <TrashIcon width={16} height={16} />
          </button>
        )}
      />
    </ThreadListItemPrimitive.Root>
  );
};

export function ClaudeThreadList({
  onSelectThread,
  onCloseSidebar,
  onExpandSidebar,
  onCollapseSidebar,
  variant = "expanded",
}: {
  onSelectThread?: () => void;
  onCloseSidebar?: () => void;
  onExpandSidebar?: () => void;
  onCollapseSidebar?: () => void;
  variant?: "expanded" | "collapsed";
}) {
  const api = useAssistantApi();
  const router = useRouter();
  const pathname = usePathname();
  const activeRemoteId = useAssistantState(
    ({ threads }: any) => getActiveThreadListItem(threads)?.remoteId ?? null,
  );
  const activeStatus = useAssistantState(
    ({ threads }: any) => getActiveThreadListItem(threads)?.status ?? "new",
  );
  const threadIds = useAssistantState(({ threads }: any) => threads?.threadIds ?? []);
  const threadItems = useAssistantState(({ threads }: any) => threads?.threadItems ?? []);

  const uniqueThreadItems = useMemo(() => {
    const idToItem = new Map<string, any>();
    for (const item of threadItems) {
      if (item && typeof item.id === "string") {
        idToItem.set(item.id, item);
      }
    }

    const seenRemoteIds = new Set<string>();
    const result: Array<{ id: string; remoteId: string; title?: string; status: "regular" | "archived" }> = [];

    for (const id of threadIds) {
      if (typeof id !== "string") continue;
      const item = idToItem.get(id);
      const remoteId = item?.remoteId;
      const title = typeof item?.title === "string" ? item.title.trim() : "";

      if (typeof remoteId !== "string" || !remoteId) {
        continue;
      }

      if (seenRemoteIds.has(remoteId)) {
        continue;
      }

      seenRemoteIds.add(remoteId);
      result.push({ id, remoteId, title: title || undefined, status: item?.status === "archived" ? "archived" : "regular" });
    }

    return result;
  }, [threadIds, threadItems]);

  const handleNewChat = async () => {
    const shouldSwitchToNewThread = activeStatus !== "new" || Boolean(activeRemoteId);
    const isAssistantRoute = pathname === "/assistant" || pathname.startsWith("/assistant/");

    if (isAssistantRoute && shouldSwitchToNewThread) {
      await api.threads().switchToNewThread();

      // Update URL without triggering Next.js navigation (avoids double render)
      if (pathname !== "/assistant") {
        window.history.replaceState(window.history.state, "", "/assistant");
      }

      onSelectThread?.();
      return;
    }

    if (pathname !== "/assistant") {
      router.push("/assistant", { scroll: false });
      onSelectThread?.();
      return;
    }

    onSelectThread?.();
  };

  const user = getAuthUser();
  const name = user ? formatClaudeGreetingName(user) : "NOA User";
  const initial = name.trim().slice(0, 1).toUpperCase() || "U";
  const secondary = user?.email?.trim() || user?.roles?.join(", ") || "Signed in";
  const isAdminUser = Boolean(user?.roles?.includes("admin"));

  if (variant === "collapsed") {
    const railButtonClassName =
      "flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-2 focus-visible:ring-offset-background";

    const RailItem: FC<{ label: string; children: ReactNode }> = ({ label, children }) => {
      return (
        <div className="group relative flex">
          {children}
          <div
            aria-hidden="true"
            className={[
              "pointer-events-none absolute top-1/2 left-full z-20 ml-2 -translate-y-1/2",
               "whitespace-nowrap rounded-md border border-sidebar-border bg-popover px-2 py-1 font-sans text-xs text-foreground shadow-sm",
              "opacity-0 translate-x-1 transition",
              "group-hover:translate-x-0 group-hover:opacity-100",
              "group-focus-within:translate-x-0 group-focus-within:opacity-100",
            ].join(" ")}
          >
            {label}
          </div>
        </div>
      );
    };

    const DisabledRailButton: FC<{ label: string; icon: ReactNode }> = ({ label, icon }) => {
      return (
        <RailItem label={`${label} (coming soon)`}>
          <button
            type="button"
            aria-disabled="true"
            title="Coming soon"
            onClick={(event) => event.preventDefault()}
            className={railButtonClassName}
          >
            <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center">
              {icon}
            </span>
          </button>
        </RailItem>
      );
    };

    return (
      <ThreadListPrimitive.Root className="flex h-full flex-col bg-sidebar py-3">
        <div className="flex flex-1 flex-col items-center gap-1">
          {onExpandSidebar ? (
            <RailItem label="Expand sidebar">
              <button
                type="button"
                onClick={onExpandSidebar}
                aria-label="Expand sidebar"
                className={railButtonClassName}
              >
                <ColumnsIcon width={14} height={14} />
              </button>
            </RailItem>
          ) : null}

          <RailItem label="New chat">
            <button
              type="button"
              onClick={handleNewChat}
              aria-label="New chat"
              className={[railButtonClassName, "mt-4"].join(" ")}
            >
              <PlusIcon width={14} height={14} />
            </button>
          </RailItem>

          <DisabledRailButton
            label="Search"
            icon={<MagnifyingGlassIcon width={14} height={14} />}
          />

          <div className="mt-auto flex flex-col items-center gap-2 pt-3">
            {isAdminUser ? (
              <RailItem label="Admin">
                <Link
                  href="/admin"
                  aria-label="Admin"
                  className={railButtonClassName}
                >
                  <GearIcon width={14} height={14} />
                </Link>
              </RailItem>
            ) : null}

            <RailItem label="Account">
              <AccountMenu
                onLogout={clearAuth}
                trigger={
                  <button
                    type="button"
                    aria-label="Account menu"
                    className={railButtonClassName}
                  >
                    <span
                      aria-hidden="true"
                      className="flex h-8 w-8 items-center justify-center rounded-full bg-foreground font-sans text-sm font-semibold text-background"
                    >
                      {initial}
                    </span>
                  </button>
                }
              />
            </RailItem>
          </div>
        </div>
      </ThreadListPrimitive.Root>
    );
  }

  const closeAction = onCollapseSidebar ?? onCloseSidebar;
  const closeActionLabel = onCollapseSidebar ? "Collapse sidebar" : "Close sidebar";

  return (
    <ThreadListPrimitive.Root className="flex h-full flex-col border-r border-sidebar-border/80 bg-sidebar">
      <div className="pt-3 font-sans">
        <div className="flex items-center justify-between px-4">
          <div className="font-serif text-lg font-semibold tracking-[-0.01em] text-foreground">
            NOA
          </div>

          {closeAction ? (
            <button
              type="button"
              onClick={closeAction}
              aria-label={closeActionLabel}
                className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground active:scale-[0.98]"
            >
              <ColumnsIcon width={18} height={18} />
            </button>
          ) : (
            <div aria-hidden="true" className="h-9 w-9" />
          )}
        </div>

        <div className="mt-3">
          <button
            type="button"
            onClick={handleNewChat}
            className="flex w-full items-center gap-3 rounded-xl px-4 py-2 font-sans text-sm text-foreground transition-colors hover:bg-sidebar-accent active:scale-[0.99]"
          >
            <span
              aria-hidden="true"
               className="flex h-6 w-6 items-center justify-center rounded-full border border-sidebar-border bg-card text-muted-foreground shadow-sm"
            >
              <PlusIcon width={14} height={14} />
            </span>
            New chat
          </button>

          <div className="mt-2">
            <DisabledNavItem icon={<MagnifyingGlassIcon width={16} height={16} />} label="Search" />
          </div>
        </div>
      </div>

      <div className="mt-4 flex min-h-0 flex-1 flex-col font-sans">
        <p className="px-4 pb-2 text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">Recents</p>
        <div className="min-h-0 flex-1 overflow-y-auto pb-3 pr-2 [scrollbar-gutter:stable]">
          {uniqueThreadItems.map((item, index) => (
            <ThreadListItemByIndexProvider
              key={`${item.remoteId}:${item.id}`}
              index={index}
              archived={item.status === "archived"}
            >
              <ThreadListItem
                onSelect={onSelectThread}
                activeRemoteId={activeRemoteId}
                itemId={item.id}
                remoteId={item.remoteId}
                threadTitle={item.title}
              />
            </ThreadListItemByIndexProvider>
          ))}
        </div>
      </div>

      <div className="border-sidebar-border/80 border-t bg-sidebar/70 px-4 py-3 font-sans backdrop-blur-sm">
        {isAdminUser ? (
          <div className="mb-2">
            <NavLinkItem icon={<GearIcon width={16} height={16} />} label="Admin" href="/admin" />
          </div>
        ) : null}

        <AccountMenu
          onLogout={clearAuth}
          trigger={
            <button
              type="button"
              aria-label="Account menu"
              className="flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left transition-colors hover:bg-sidebar-accent active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30 focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-foreground text-sm font-semibold text-background">
                {initial}
              </div>

              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-foreground">{name}</p>
                <p className="truncate text-xs text-muted-foreground">{secondary}</p>
              </div>
            </button>
          }
        />
      </div>
    </ThreadListPrimitive.Root>
  );
}
