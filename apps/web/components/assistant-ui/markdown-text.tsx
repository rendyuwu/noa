"use client";

import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";

import "@assistant-ui/react-markdown/styles/dot.css";

import { ScrollArea } from "@/components/ui/scroll-area";

function mergeClassNames(...values: Array<string | undefined>): string {
  return values.filter(Boolean).join(" ");
}

// Used as a MessagePrimitive.Parts Text renderer.
export const MarkdownText = (_props: any) => {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ node: _node, className, ...props }: any) => (
          <h1
            {...props}
            className={mergeClassNames(
              "mt-6 mb-3 font-serif text-[1.95em] leading-[1.1] tracking-[-0.025em] text-foreground",
              className,
            )}
          />
        ),
        h2: ({ node: _node, className, ...props }: any) => (
          <h2
            {...props}
            className={mergeClassNames(
              "mt-5 mb-2 font-serif text-[1.45em] leading-[1.2] tracking-[-0.02em] text-foreground",
              className,
            )}
          />
        ),
        p: ({ node: _node, className, ...props }: any) => (
          <p
            {...props}
            className={mergeClassNames("my-4 text-[15px] leading-7 text-foreground/90", className)}
          />
        ),
        table: ({ className, node: _node, ...props }: any) => (
          <ScrollArea
            data-testid="md-table-scroll"
            className="my-2 w-full rounded-xl border border-border bg-card/60 shadow-sm backdrop-blur-sm"
          >
            <table
              {...props}
              className={[
                "w-max min-w-full border-collapse text-sm",
                "[&_th]:whitespace-nowrap [&_td]:whitespace-nowrap",
                "[&_th]:px-3 [&_td]:px-3 [&_th]:py-2 [&_td]:py-2",
                "[&_th]:text-left [&_th]:font-semibold",
                "[&_tr]:border-b [&_tr]:border-border/60",
                className ?? "",
              ].join(" ")}
            />
          </ScrollArea>
        ),
      }}
      className={[
        "[&_:is(ul,ol,pre,blockquote,table)]:my-2",
        "[&_:is(h1,h2,h3,h4,h5,h6,p)]:mx-0",
        "[&_pre]:overflow-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-border [&_pre]:bg-accent [&_pre]:p-2",
        "[&_code:not(pre_code)]:rounded-md [&_code:not(pre_code)]:bg-accent/70 [&_code:not(pre_code)]:px-1 [&_code:not(pre_code)]:py-0.5",
        "[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
      ].join(" ")}
    />
  );
};
