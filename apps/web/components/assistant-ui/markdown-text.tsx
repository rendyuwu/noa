"use client";

import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";

import "@assistant-ui/react-markdown/styles/dot.css";

// Used as a MessagePrimitive.Parts Text renderer.
export const MarkdownText = (_props: any) => {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={[remarkGfm]}
      components={{
        table: ({ className, ...props }) => (
          <div
            data-testid="md-table-scroll"
            className="my-2 w-full overflow-x-auto overflow-y-hidden rounded-xl border border-[#00000015] bg-white/60 shadow-sm backdrop-blur-sm dark:border-[#6c6a6040] dark:bg-[#1f1e1b]/40"
          >
            <table
              {...props}
              className={[
                "w-max min-w-full border-collapse text-sm",
                "[&_th]:whitespace-nowrap [&_td]:whitespace-nowrap",
                "[&_th]:px-3 [&_td]:px-3 [&_th]:py-2 [&_td]:py-2",
                "[&_th]:text-left [&_th]:font-semibold",
                "[&_tr]:border-b [&_tr]:border-[#00000010] dark:[&_tr]:border-[#6c6a6040]",
                className ?? "",
              ].join(" ")}
            />
          </div>
        ),
      }}
      className={[
        "[&_:is(p,ul,ol,pre,blockquote,table)]:my-2",
        "[&_pre]:overflow-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-[#00000015] [&_pre]:bg-[#f5f5f0] [&_pre]:p-2",
        "dark:[&_pre]:border-[#6c6a6040] dark:[&_pre]:bg-[#393937]",
        "[&_code:not(pre_code)]:rounded-md [&_code:not(pre_code)]:bg-[#00000008] [&_code:not(pre_code)]:px-1 [&_code:not(pre_code)]:py-0.5",
        "dark:[&_code:not(pre_code)]:bg-[#ffffff10]",
        "[&_blockquote]:border-l-2 [&_blockquote]:border-[#00000015] [&_blockquote]:pl-3 [&_blockquote]:text-[#4b4a48]",
        "dark:[&_blockquote]:border-[#6c6a6040] dark:[&_blockquote]:text-[#c9c6bd]",
      ].join(" ")}
    />
  );
};
