import type { ReactNode } from "react";

import { AssistantApp } from "@/components/assistant/assistant-app";

export default function AssistantLayout({ children }: { children: ReactNode }) {
  return <AssistantApp>{children}</AssistantApp>;
}
