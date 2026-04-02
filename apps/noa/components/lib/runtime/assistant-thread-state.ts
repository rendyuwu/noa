export function getActiveThreadListItem(
  threads: { mainThreadId?: string | null; threadItems?: Array<{ id?: string | null }> } | null | undefined,
) {
  const mainThreadId = typeof threads?.mainThreadId === "string" ? threads.mainThreadId : null;
  const threadItems = Array.isArray(threads?.threadItems) ? threads.threadItems : [];

  if (!mainThreadId) {
    return null;
  }

  return threadItems.find((item) => item?.id === mainThreadId) ?? null;
}
