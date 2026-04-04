export type GroupableThread = {
  id: string;
  updatedAt?: string | null;
};

type ThreadGroup<T extends GroupableThread> = {
  label: string;
  threads: T[];
};

const GROUP_ORDER = ["Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older"] as const;

function getGroupLabel(dateStr: string | null | undefined): string {
  if (!dateStr) return "Older";

  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "Older";

  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfYesterday = new Date(startOfToday.getTime() - 86_400_000);
  const startOf7DaysAgo = new Date(startOfToday.getTime() - 7 * 86_400_000);
  const startOf30DaysAgo = new Date(startOfToday.getTime() - 30 * 86_400_000);

  if (date >= startOfToday) return "Today";
  if (date >= startOfYesterday) return "Yesterday";
  if (date >= startOf7DaysAgo) return "Previous 7 days";
  if (date >= startOf30DaysAgo) return "Previous 30 days";
  return "Older";
}

export function groupThreadsByDate<T extends GroupableThread>(threads: T[]): ThreadGroup<T>[] {
  const buckets = new Map<string, T[]>();

  for (const thread of threads) {
    const label = getGroupLabel(thread.updatedAt);
    const bucket = buckets.get(label);
    if (bucket) {
      bucket.push(thread);
    } else {
      buckets.set(label, [thread]);
    }
  }

  const result: ThreadGroup<T>[] = [];
  for (const label of GROUP_ORDER) {
    const threads = buckets.get(label);
    if (threads && threads.length > 0) {
      result.push({ label, threads });
    }
  }

  return result;
}
