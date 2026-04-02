"use client";

import type { PropsWithChildren } from "react";
import { createContext, useContext } from "react";

type ThreadHydrationState = {
  errorMessage: string | null;
  isHydrating: boolean;
  retry: () => void;
};

const ThreadHydrationContext = createContext<ThreadHydrationState>({
  errorMessage: null,
  isHydrating: false,
  retry: () => {},
});

export function ThreadHydrationProvider({
  errorMessage,
  isHydrating,
  retry,
  children,
}: PropsWithChildren<{
  errorMessage: string | null;
  isHydrating: boolean;
  retry: () => void;
}>) {
  return (
    <ThreadHydrationContext.Provider value={{ errorMessage, isHydrating, retry }}>
      {children}
    </ThreadHydrationContext.Provider>
  );
}

export function useThreadHydration() {
  return useContext(ThreadHydrationContext);
}
