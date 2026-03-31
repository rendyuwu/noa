"use client";

import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";

import "@assistant-ui/react-markdown/styles/dot.css";

import { ScrollArea } from "@/components/lib/scroll-area";

// Used as a MessagePrimitive.Parts Text renderer.
export const MarkdownText = (_props: any) => {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={[remarkGfm]}
      components={{
        table: ({ className, node: _node, ...props }: any) => (
          <ScrollArea
            data-testid="md-table-scroll"
            className="my-2 w-full rounded-xl border border-border bg-surface/60 shadow-sm backdrop-blur-sm"
            verticalScrollbar={false}
            horizontalScrollbar
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
        "[&_:is(p,ul,ol,pre,blockquote,table)]:my-2",
        "[&_pre]:overflow-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-border [&_pre]:bg-surface-2 [&_pre]:p-2",
        "[&_code:not(pre_code)]:rounded-md [&_code:not(pre_code)]:bg-surface-2/70 [&_code:not(pre_code)]:px-1 [&_code:not(pre_code)]:py-0.5",
        "[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted",
      ].join(" ")}
    />
  );
};
