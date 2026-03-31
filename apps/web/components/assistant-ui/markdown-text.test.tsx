import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

let lastProps: any = null;

vi.mock("@assistant-ui/react-markdown", () => {
  return {
    MarkdownTextPrimitive: (props: any) => {
      lastProps = props;
      return null;
    },
  };
});

import { MarkdownText } from "./markdown-text";

describe("MarkdownText", () => {
  it("provides a horizontally scrollable table wrapper", () => {
    render(<MarkdownText />);

    expect(lastProps?.components?.table).toBeTypeOf("function");

    const Table = lastProps.components.table;
    const { container } = render(
      <Table>
        <tbody>
          <tr>
            <td>Hello</td>
          </tr>
        </tbody>
      </Table>,
    );

    const scroll = container.querySelector("[data-testid='md-table-scroll']");
    expect(scroll).toBeInTheDocument();
    expect(scroll).toHaveAttribute("data-slot", "scroll-area");
  });
});
