"use client";

import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";

import "@assistant-ui/react-markdown/styles/dot.css";

// Used as a MessagePrimitive.Parts Text renderer.
export const MarkdownText = (_props: any) => {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={[remarkGfm]}
      className="[&_:is(p,ul,ol,pre,blockquote)]:my-2 [&_pre]:overflow-auto [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-[#00000015] [&_pre]:bg-[#f5f5f0] [&_pre]:p-2 dark:[&_pre]:border-[#6c6a6040] dark:[&_pre]:bg-[#393937]"
    />
  );
};
