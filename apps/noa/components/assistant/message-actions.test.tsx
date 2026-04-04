import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { TooltipProvider } from "@/components/ui/tooltip";

import { MessageActions } from "./message-actions";

function Wrapper({ children }: { children: React.ReactNode }) {
  return <TooltipProvider>{children}</TooltipProvider>;
}

describe("MessageActions", () => {
  beforeEach(() => {
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });
  });

  it("renders copy button", () => {
    render(<MessageActions content="Hello world" />, { wrapper: Wrapper });
    expect(screen.getByLabelText("Copy")).toBeInTheDocument();
  });

  it("copies content to clipboard on click", async () => {
    render(<MessageActions content="Hello world" />, { wrapper: Wrapper });
    fireEvent.click(screen.getByLabelText("Copy"));
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith("Hello world");
    });
  });

  it("shows check icon after copy", async () => {
    render(<MessageActions content="Hello world" />, { wrapper: Wrapper });
    fireEvent.click(screen.getByLabelText("Copy"));
    await waitFor(() => {
      expect(screen.getByLabelText("Copied")).toBeInTheDocument();
    });
  });

  it("renders thumbs up and thumbs down", () => {
    render(<MessageActions content="test" />, { wrapper: Wrapper });
    expect(screen.getByLabelText("Helpful")).toBeInTheDocument();
    expect(screen.getByLabelText("Not helpful")).toBeInTheDocument();
  });
});
