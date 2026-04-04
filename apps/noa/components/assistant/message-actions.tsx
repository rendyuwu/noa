"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Copy, ThumbsDown, ThumbsUp } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

type MessageActionsProps = {
  content: string;
};

export function MessageActions({ content }: MessageActionsProps) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
    } catch {
      // Silently fail — clipboard may not be available
    }
  }, [content]);

  return (
    <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover/msg:opacity-100 [.group\/msg:focus-within_&]:opacity-100">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-7 rounded-md text-muted hover:text-text"
            onClick={handleCopy}
            aria-label={copied ? "Copied" : "Copy"}
          >
            {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{copied ? "Copied!" : "Copy"}</TooltipContent>
      </Tooltip>

      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-7 rounded-md text-muted hover:text-text"
            aria-label="Helpful"
            onClick={() => {
              /* placeholder — wire up feedback API later */
            }}
          >
            <ThumbsUp className="size-3.5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Helpful</TooltipContent>
      </Tooltip>

      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-7 rounded-md text-muted hover:text-text"
            aria-label="Not helpful"
            onClick={() => {
              /* placeholder — wire up feedback API later */
            }}
          >
            <ThumbsDown className="size-3.5" />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Not helpful</TooltipContent>
      </Tooltip>
    </div>
  );
}
