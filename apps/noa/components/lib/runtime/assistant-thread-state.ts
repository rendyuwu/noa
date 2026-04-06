export function getActiveThreadListItem(
  threads:
    | {
        mainThreadId?: string | null;
        threadItems?: ReadonlyArray<{
          id?: string | null;
          remoteId?: string | null;
          externalId?: string | null;
          status?: string | null;
          title?: string | null;
        }>;
      }
    | null
    | undefined,
) {
  const mainThreadId = typeof threads?.mainThreadId === "string" ? threads.mainThreadId : null;
  const threadItems = Array.isArray(threads?.threadItems) ? threads.threadItems : [];

  if (!mainThreadId) {
    return null;
  }

  return (
    threadItems.find((item) => item?.id === mainThreadId) ??
    threadItems.find((item) => item?.remoteId === mainThreadId || item?.externalId === mainThreadId) ??
    null
  );
}
