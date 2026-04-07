import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TruncatedText } from "@/components/assistant/inline-disclosure";

import { ThinkingBlock } from "./thinking-block";

describe("ThinkingBlock", () => {
  it("starts collapsed", () => {
    render(
      <ThinkingBlock>
        <div>Reasoning summary</div>
      </ThinkingBlock>,
    );

    expect(screen.getByRole("button", { name: "Thinking" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.getByText("Reasoning summary")).not.toBeVisible();
  });

  it("expands and supports truncated previews for long text", () => {
    const longText = Array.from({ length: 20 }, (_, index) => `Line ${index + 1}`).join("\n");

    render(
      <ThinkingBlock>
        <TruncatedText text={longText} initialLines={2} />
      </ThinkingBlock>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Thinking" }));

    expect(screen.getByRole("button", { name: "Thinking" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByRole("button", { name: "Show full" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Show full" }));

    expect(screen.getByRole("button", { name: "Show less" })).toBeVisible();
  });
});
