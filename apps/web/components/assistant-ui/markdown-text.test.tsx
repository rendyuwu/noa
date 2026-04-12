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

    expect(lastProps?.components?.h1).toBeTypeOf("function");
    expect(lastProps?.components?.p).toBeTypeOf("function");
    expect(lastProps?.components?.table).toBeTypeOf("function");

    const H1 = lastProps.components.h1;
    const P = lastProps.components.p;

    const { container: headingContainer } = render(<H1>Editorial heading</H1>);
    const heading = headingContainer.querySelector("h1");
    expect(heading).toHaveClass("font-serif");
    expect(heading).toHaveClass("tracking-[-0.025em]");

    const { container: paragraphContainer } = render(<P>Editorial body</P>);
    const paragraph = paragraphContainer.querySelector("p");
    expect(paragraph).toHaveClass("leading-7");
    expect(paragraph).toHaveClass("text-[15px]");

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
