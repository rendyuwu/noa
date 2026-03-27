"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import type { FC, ReactNode } from "react";

import {
  ThreadListItemByIdProvider,
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  useAssistantApi,
  useAssistantState,
} from "@assistant-ui/react";
import {
  ArchiveIcon,
  ActivityLogIcon,
  ChevronDownIcon,
  ColumnsIcon,
  CodeIcon,
  DesktopIcon,
  ExitIcon,
  GearIcon,
  MagnifyingGlassIcon,
  PersonIcon,
  PlusIcon,
  TrashIcon,
  IdCardIcon,
} from "@radix-ui/react-icons";

import { formatClaudeGreetingName } from "@/components/assistant/claude-greeting";
import { clearAuth, getAuthUser } from "@/components/lib/auth-store";
import { ConfirmAction } from "@/components/lib/confirm-dialog";

function DisabledNavItem({ icon, label }: { icon: ReactNode; label: string }) {
  return (
    <button
      type="button"
      aria-disabled="true"
      title="Coming soon"
      onClick={(event) => event.preventDefault()}
      className="flex w-full items-center justify-start gap-3 rounded-lg px-4 py-2 font-ui text-sm text-muted opacity-70 transition-colors hover:bg-surface-2/60 hover:text-text"
    >
      <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center">
        {icon}
      </span>
      {label}
    </button>
  );
}

function NavLinkItem({
  icon,
  label,
  href,
}: {
  icon: ReactNode;
  label: string;
  href: string;
}) {
  return (
    <Link
      href={href}
      className="flex w-full items-center justify-start gap-3 rounded-lg px-4 py-2 font-ui text-sm text-muted transition-colors hover:bg-surface-2/60 hover:text-text active:scale-[0.99]"
    >
      <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center">
        {icon}
      </span>
      {label}
    </Link>
  );
}

const ThreadListItem: FC<{ onSelect?: () => void }> = ({ onSelect }) => {
  const api = useAssistantApi();
  const router = useRouter();
  const pathname = usePathname();
  const remoteId = useAssistantState(({ threadListItem }) => threadListItem.remoteId);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  if (!remoteId) {
    return null;
  }

  return (
    <ThreadListItemPrimitive.Root className="group flex items-center gap-2 rounded-lg px-4 py-2 transition-colors hover:bg-surface-2/60 data-[active]:bg-surface-2/60">
      <ThreadListItemPrimitive.Trigger
        onClick={() => {
          onSelect?.();

          const href = `/assistant/${remoteId}`;

          void (async () => {
            try {
              await api.threadListItem().switchTo();
            } catch (error) {
              console.error("Failed to switch to selected thread", error);
            }

            if (pathname !== href) {
              router.push(href);
            }
          })();
        }}
        className="min-w-0 flex-1 rounded-md text-left font-ui text-sm text-text outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
      >
        <span className="block truncate">
          <ThreadListItemPrimitive.Title fallback="Untitled" />
        </span>
      </ThreadListItemPrimitive.Trigger>

      <ConfirmAction
        title="Delete thread?"
        description="This permanently deletes this thread."
        confirmLabel="Delete thread"
        confirmVariant="danger"
        error={deleteError}
        onConfirm={async () => {
          setDeleteError(null);

          const itemApi = api.threadListItem();

          if (pathname === `/assistant/${remoteId}`) {
            router.replace("/assistant");
            try {
              await api.threads().switchToNewThread();
            } catch (error) {
              console.error("Failed to switch away from thread before deletion", error);
            }
          }

          try {
            await itemApi.delete();
          } catch (error) {
            setDeleteError(error instanceof Error ? error.message : "Failed to delete thread.");
          }
        }}
        trigger={({ open, disabled }) => (
          <ThreadListItemPrimitive.Delete
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted opacity-0 transition hover:bg-surface-2/60 hover:text-text group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
            aria-label="Delete thread"
            disabled={disabled}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              open();
            }}
          >
            <TrashIcon width={16} height={16} />
          </ThreadListItemPrimitive.Delete>
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

  const threadIds = useAssistantState(({ threads }: any) => threads?.threadIds ?? []);
  const threadItems = useAssistantState(({ threads }: any) => threads?.threadItems ?? []);

  const uniqueThreadIds = useMemo(() => {
    const idToItem = new Map<string, any>();
    for (const item of threadItems) {
      if (item && typeof item.id === "string") {
        idToItem.set(item.id, item);
      }
    }

    const seenRemoteIds = new Set<string>();
    const result: string[] = [];

    for (const id of threadIds) {
      if (typeof id !== "string") continue;
      const item = idToItem.get(id);
      const remoteId = item?.remoteId;

      if (typeof remoteId !== "string" || !remoteId) {
        continue;
      }

      if (seenRemoteIds.has(remoteId)) {
        continue;
      }

      seenRemoteIds.add(remoteId);
      result.push(id);
    }

    return result;
  }, [threadIds, threadItems]);

  const handleNewChat = async () => {
    await api.threads().switchToNewThread();

    if (pathname !== "/assistant") {
      router.push("/assistant");
    }

    onSelectThread?.();
  };

  const user = getAuthUser();
  const name = user ? formatClaudeGreetingName(user) : "NOA User";
  const initial = name.trim().slice(0, 1).toUpperCase() || "U";
  const secondary = user?.email?.trim() || user?.roles?.join(", ") || "Signed in";
  const isAdmin = user?.roles?.includes("admin") ?? false;

  if (variant === "collapsed") {
    const railButtonClassName =
      "flex h-9 w-9 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface-2/60 hover:text-text active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg";

    const RailItem: FC<{ label: string; children: ReactNode }> = ({ label, children }) => {
      return (
        <div className="group relative flex">
          {children}
          <div
            aria-hidden="true"
            className={[
              "pointer-events-none absolute top-1/2 left-full z-20 ml-2 -translate-y-1/2",
              "whitespace-nowrap rounded-md border border-border bg-surface px-2 py-1 font-ui text-xs text-text shadow-sm",
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
      <ThreadListPrimitive.Root className="flex h-full flex-col bg-bg py-3">
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

          {isAdmin ? (
            <RailItem label="Users">
              <Link href="/admin/users" aria-label="Users" className={railButtonClassName}>
                <PersonIcon width={14} height={14} />
              </Link>
            </RailItem>
          ) : null}

          {isAdmin ? (
            <RailItem label="Roles">
              <Link href="/admin/roles" aria-label="Roles" className={railButtonClassName}>
                <IdCardIcon width={14} height={14} />
              </Link>
            </RailItem>
          ) : null}

          {isAdmin ? (
            <RailItem label="Audit">
              <Link href="/admin/audit" aria-label="Audit" className={railButtonClassName}>
                <ActivityLogIcon width={14} height={14} />
              </Link>
            </RailItem>
          ) : null}

          {isAdmin ? (
            <RailItem label="WHM Servers">
              <Link
                href="/admin/whm/servers"
                aria-label="WHM Servers"
                className={railButtonClassName}
              >
                <DesktopIcon width={14} height={14} />
              </Link>
            </RailItem>
          ) : null}

          <DisabledRailButton label="Artifacts" icon={<ArchiveIcon width={14} height={14} />} />
          <DisabledRailButton label="Code" icon={<CodeIcon width={14} height={14} />} />

          <div className="mt-auto flex flex-col items-center gap-2 pt-3">
            <RailItem label={`${name} • ${secondary}`}>
              <div
                aria-hidden="true"
                className="flex h-9 w-9 items-center justify-center rounded-full bg-text font-ui text-sm font-semibold text-bg"
              >
                {initial}
              </div>
            </RailItem>

            <ConfirmAction
              title="Log out?"
              description="This ends your NOA session on this device."
              confirmLabel="Log out"
              confirmVariant="primary"
              onConfirm={clearAuth}
              trigger={({ open, disabled }) => (
                <RailItem label="Logout">
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={open}
                    aria-label="Logout"
                    className={railButtonClassName}
                  >
                    <ExitIcon width={14} height={14} />
                  </button>
                </RailItem>
              )}
            />
          </div>
        </div>
      </ThreadListPrimitive.Root>
    );
  }

  const [backendOpen, setBackendOpen] = useState(false);

  const closeAction = onCollapseSidebar ?? onCloseSidebar;
  const closeActionLabel = onCollapseSidebar ? "Collapse sidebar" : "Close sidebar";

  return (
    <ThreadListPrimitive.Root className="flex h-full flex-col bg-bg">
      <div className="pt-3 font-ui">
        <div className="flex items-center justify-between px-4">
          <div className="font-serif text-lg font-semibold tracking-[-0.01em] text-text">
            NOA
          </div>

          {closeAction ? (
            <button
              type="button"
              onClick={closeAction}
              aria-label={closeActionLabel}
              className="flex h-9 w-9 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface-2/60 hover:text-text active:scale-[0.98]"
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
            className="flex w-full items-center gap-3 rounded-lg px-4 py-2 font-ui text-sm text-text transition-colors hover:bg-surface-2/60 active:scale-[0.99]"
          >
            <span
              aria-hidden="true"
              className="flex h-6 w-6 items-center justify-center rounded-full border border-border bg-surface text-muted"
            >
              <PlusIcon width={14} height={14} />
            </span>
            New chat
          </button>

          <div className="mt-2">
            <DisabledNavItem icon={<MagnifyingGlassIcon width={16} height={16} />} label="Search" />
            {isAdmin ? (
              <NavLinkItem icon={<PersonIcon width={16} height={16} />} label="Users" href="/admin/users" />
            ) : null}
            {isAdmin ? (
              <NavLinkItem icon={<IdCardIcon width={16} height={16} />} label="Roles" href="/admin/roles" />
            ) : null}
            {isAdmin ? (
              <NavLinkItem icon={<ActivityLogIcon width={16} height={16} />} label="Audit" href="/admin/audit" />
            ) : null}
            {isAdmin ? (
              <div>
                <button
                  type="button"
                  onClick={() => setBackendOpen((prev) => !prev)}
                  aria-expanded={backendOpen}
                  aria-controls="backend-nav"
                  className="flex w-full items-center justify-start gap-3 rounded-lg px-4 py-2 font-ui text-sm text-muted transition-colors hover:bg-surface-2/60 hover:text-text active:scale-[0.99]"
                >
                  <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center">
                    <GearIcon width={16} height={16} />
                  </span>
                  <span className="flex-1 text-left">Backend</span>
                  <span
                    aria-hidden="true"
                    className={[
                      "flex h-4 w-4 items-center justify-center text-muted transition-transform",
                      backendOpen ? "rotate-180" : "rotate-0",
                    ].join(" ")}
                  >
                    <ChevronDownIcon width={16} height={16} />
                  </span>
                </button>
                {backendOpen ? (
                  <div id="backend-nav" className="mt-1 pl-4">
                    <NavLinkItem
                      icon={<DesktopIcon width={16} height={16} />}
                      label="WHM Servers"
                      href="/admin/whm/servers"
                    />
                  </div>
                ) : (
                  <div id="backend-nav" className="hidden" />
                )}
              </div>
            ) : null}
            <DisabledNavItem icon={<ArchiveIcon width={16} height={16} />} label="Artifacts" />
            <DisabledNavItem icon={<CodeIcon width={16} height={16} />} label="Code" />
          </div>
        </div>
      </div>

      <div className="mt-4 flex min-h-0 flex-1 flex-col font-ui">
        <p className="px-4 pb-2 text-xs font-medium uppercase tracking-[0.12em] text-muted">Recents</p>
        <div className="min-h-0 flex-1 overflow-y-auto pb-3">
          {uniqueThreadIds.map((id) => (
            <ThreadListItemByIdProvider key={id} id={id}>
              <ThreadListItem onSelect={onSelectThread} />
            </ThreadListItemByIdProvider>
          ))}
        </div>
      </div>

      <div className="border-border border-t px-4 py-3 font-ui">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-text text-sm font-semibold text-bg">
            {initial}
          </div>

          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-text">{name}</p>
            <p className="truncate text-xs text-muted">{secondary}</p>
          </div>
        </div>

        <div className="mt-2 flex items-center justify-end gap-3">
          <ConfirmAction
            title="Log out?"
            description="This ends your NOA session on this device."
            confirmLabel="Log out"
            confirmVariant="primary"
            onConfirm={clearAuth}
            trigger={({ open, disabled }) => (
              <button
                type="button"
                disabled={disabled}
                onClick={open}
                className="text-sm text-muted underline decoration-border/60 underline-offset-4 hover:text-text hover:decoration-border disabled:opacity-55"
              >
                Logout
              </button>
            )}
          />
        </div>
      </div>
    </ThreadListPrimitive.Root>
  );
}
