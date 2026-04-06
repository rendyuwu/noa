export function getActiveThreadListItem(threads: any) {
  const mainThreadId = typeof threads?.mainThreadId === "string" ? threads.mainThreadId : null;
  const threadItems = Array.isArray(threads?.threadItems) ? threads.threadItems : [];

  if (!mainThreadId) {
    return null;
  }

  return threadItems.find((item: any) => item?.id === mainThreadId) ?? null;
}
