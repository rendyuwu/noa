import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { TooltipProvider } from "@/components/ui/tooltip";

import { ChatSidebarNav } from "./chat-sidebar-nav";

function Wrapper({ children }: { children: React.ReactNode }) {
  return <TooltipProvider>{children}</TooltipProvider>;
}

describe("ChatSidebarNav", () => {
  it("renders the search placeholder", () => {
    render(<ChatSidebarNav />, { wrapper: Wrapper });
    expect(screen.getByPlaceholderText("Search")).toBeInTheDocument();
  });

  it("renders all nav items", () => {
    render(<ChatSidebarNav />, { wrapper: Wrapper });
    expect(screen.getByText("Projects")).toBeInTheDocument();
    expect(screen.getByText("Artifacts")).toBeInTheDocument();
    expect(screen.getByText("Code")).toBeInTheDocument();
  });

  it("marks placeholder items as disabled", () => {
    render(<ChatSidebarNav />, { wrapper: Wrapper });
    const projects = screen.getByText("Projects").closest("button");
    expect(projects).toHaveAttribute("aria-disabled", "true");
  });
});
