"use client";

import { useAuthSession } from "./auth-session";

export function useRequireAuth() {
  return useAuthSession();
}
