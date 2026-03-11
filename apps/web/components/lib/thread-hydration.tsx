"use client";

import type { PropsWithChildren } from "react";
import { createContext, useContext } from "react";

type ThreadHydrationState = {
  isHydrating: boolean;
};

const ThreadHydrationContext = createContext<ThreadHydrationState>({ isHydrating: false });

export function ThreadHydrationProvider({
  isHydrating,
  children,
}: PropsWithChildren<{ isHydrating: boolean }>) {
  return (
    <ThreadHydrationContext.Provider value={{ isHydrating }}>
      {children}
    </ThreadHydrationContext.Provider>
  );
}

export function useThreadHydration() {
  return useContext(ThreadHydrationContext);
}
