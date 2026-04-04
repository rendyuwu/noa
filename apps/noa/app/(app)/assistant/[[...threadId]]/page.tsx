import { AssistantWorkspace } from "@/components/assistant/assistant-workspace";

export default async function AssistantPage({
  params,
}: {
  params: Promise<{ threadId?: string[] }>;
}) {
  const { threadId } = await params;
  return <AssistantWorkspace threadId={threadId?.[0] ?? null} />;
}
