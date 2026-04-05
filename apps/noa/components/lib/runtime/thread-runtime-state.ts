export type ThreadRuntimeStateInput = {
  remoteId: string | null;
  messageCount: number;
  hydratedRemoteId: string | null;
  hydrationInFlightRemoteId: string | null;
  attemptedRemoteId: string | null;
  attemptedRetryVersion: number;
  retryVersion: number;
  pathname: string;
  lastRoutedRemoteId: string | null;
  hasRenderedMessage: boolean;
};

export function getThreadRuntimeState(input: ThreadRuntimeStateInput) {
  const canAttemptHydration = Boolean(input.remoteId)
    && input.messageCount === 0
    && input.hydratedRemoteId !== input.remoteId;

  const hydrationAttemptChanged = input.attemptedRemoteId !== input.remoteId
    || input.attemptedRetryVersion !== input.retryVersion;

  const shouldHydrate = Boolean(input.remoteId)
    && canAttemptHydration
    && input.hydrationInFlightRemoteId !== input.remoteId
    && hydrationAttemptChanged;

  const isHydrating = Boolean(input.remoteId)
    && (input.hydrationInFlightRemoteId === input.remoteId || shouldHydrate);

  const isAssistantRoute = input.pathname === "/assistant" || input.pathname.startsWith("/assistant/");
  const desiredPath = input.remoteId ? `/assistant/${input.remoteId}` : null;

  const shouldReplaceRoute = Boolean(
    desiredPath
      && isAssistantRoute
      && input.pathname !== desiredPath
      && input.lastRoutedRemoteId !== input.remoteId
      && input.hasRenderedMessage,
  );

  return {
    canAttemptHydration,
    shouldHydrate,
    isHydrating,
    shouldReplaceRoute,
    desiredPath,
  };
}

export function shouldShowThreadEmptyState({
  isHydrating,
  hasMessages,
}: {
  isHydrating: boolean;
  hasMessages: boolean;
}) {
  return !isHydrating && !hasMessages;
}
