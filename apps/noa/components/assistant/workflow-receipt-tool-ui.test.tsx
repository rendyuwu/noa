import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { parseWorkflowReceiptPayload, WorkflowReceiptSurface } from "./workflow-receipt-renderer";
import { WorkflowReceiptCard } from "./workflow-receipt-tool-ui";

describe("workflow receipt UI", () => {
  it("parses valid receipt payloads", () => {
    const parsed = parseWorkflowReceiptPayload({
      replyTemplate: {
        title: "Server updated",
        outcome: "changed",
        summary: "Applied configuration changes.",
      },
      evidenceSections: [
        {
          title: "Verification",
          items: [{ label: "HTTP", value: "200" }],
        },
      ],
      toolName: "whm_update_server",
    });

    expect(parsed).not.toBeNull();
    expect(parsed?.badge.label).toBe("SUCCESS");
    expect(parsed?.evidenceSections).toHaveLength(1);
  });

  it("returns null for invalid payloads", () => {
    expect(parseWorkflowReceiptPayload({})).toBeNull();
  });

  it("renders a dedicated receipt surface", () => {
    const payload = {
      replyTemplate: {
        title: "Server updated",
        outcome: "changed",
        summary: "Applied configuration changes.",
      },
      evidenceSections: [
        {
          title: "Verification",
          items: [{ label: "HTTP", value: "200" }],
        },
      ],
    };

    render(<WorkflowReceiptCard payload={payload} />);

    expect(screen.getByText("Server updated")).toBeInTheDocument();
    expect(screen.getByText("SUCCESS")).toBeInTheDocument();
    expect(screen.getByText("Verification")).toBeInTheDocument();
    expect(screen.getByText("HTTP")).toBeInTheDocument();
    expect(screen.getByText("200")).toBeInTheDocument();
  });

  it("adds receipt capture id metadata for exports", () => {
    render(
      <WorkflowReceiptSurface
        payload={{
          replyTemplate: {
            title: "No-op",
            outcome: "no_op",
            summary: "Nothing changed.",
          },
          evidenceSections: [],
        }}
        captureId="audit-1"
      />,
    );

    const captureNode = document.querySelector("[data-receipt-capture='audit-1']");
    expect(captureNode).not.toBeNull();
  });
});
