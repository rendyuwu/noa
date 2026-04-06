import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  modal: undefined as boolean | undefined,
  remoteId: "thread-1",
  title: "A very long thread title that should truncate in the sidebar row",
}));

vi.mock("@assistant-ui/react", () => ({
  ThreadListItemPrimitive: {
    Root: ({ children, ...props }: any) => (
      <div data-testid="thread-root" {...props}>
        {children}
      </div>
    ),
    Trigger: ({ children, ...props }: any) => (
      <button type="button" {...props}>
        {children}
      </button>
    ),
    Delete: ({ children }: any) => <>{children}</>,
  },
  useAssistantState: (selector: any) =>
    selector({
      threads: {
        mainThreadId: "main-thread",
        threadItems: [{ id: "main-thread", remoteId: mocks.remoteId, status: "regular", title: mocks.title }],
      },
      threadListItem: {
        remoteId: mocks.remoteId,
        title: mocks.title,
      },
    }),
}));

vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children, modal, onOpenChange: _onOpenChange, ...props }: any) => {
    mocks.modal = modal;
    return (
      <div data-testid="dropdown-menu" {...props}>
        {children}
      </div>
    );
  },
  DropdownMenuTrigger: ({ children }: any) => <>{children}</>,
  DropdownMenuContent: ({ children }: any) => <div data-testid="dropdown-menu-content">{children}</div>,
  DropdownMenuItem: ({ children }: any) => <div>{children}</div>,
}));

import { ChatThreadItem } from "./chat-thread-item";

describe("ChatThreadItem", () => {
  beforeEach(() => {
    mocks.modal = undefined;
  });

  it("keeps the actions menu non-modal and the row layout clipped", () => {
    const { container } = render(<ChatThreadItem />);

    expect(screen.getByLabelText("Thread actions")).toBeInTheDocument();
    expect(mocks.modal).toBe(false);
    expect(screen.getByTestId("thread-root")).toHaveClass("overflow-hidden", "rounded-xl");
    expect(screen.getByRole("button", { name: mocks.title })).toHaveClass("min-w-0", "flex-1");
    expect(container.querySelector("[data-testid='dropdown-menu']")).toBeInTheDocument();
  });
});
