"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { isAuthRedirectError, setAuthUser } from "@/components/lib/auth-store";
import { fetchWithAuth, jsonOrThrow } from "@/components/lib/fetch-helper";

type MeUser = {
  id: string;
  email: string;
  display_name?: string | null;
  is_active: boolean;
  roles: string[];
};

type MeResponse = {
  user: MeUser;
};

type UseVerifiedAuthOptions = {
  /** When true, redirects non-admin users to /assistant. Default: false. */
  requireAdmin?: boolean;
};

type UseVerifiedAuthResult = {
  ready: boolean;
  user: MeUser | null;
  isAdmin: boolean;
};

export function useVerifiedAuth(
  options: UseVerifiedAuthOptions = {},
): UseVerifiedAuthResult {
  const { requireAdmin = false } = options;
  const router = useRouter();
  const [state, setState] = useState<UseVerifiedAuthResult>({
    ready: false,
    user: null,
    isAdmin: false,
  });

  useEffect(() => {
    let cancelled = false;
    const abortController = new AbortController();

    void (async () => {
      try {
        const response = await fetchWithAuth("/auth/me", {
          signal: abortController.signal,
        });
        const data = await jsonOrThrow<MeResponse>(response);
        if (cancelled) return;

        const user = data.user;
        const isAdmin = user.roles.includes("admin");

        // Update the cached display user so sidebar/greeting stay fresh.
        setAuthUser({
          id: user.id,
          email: user.email,
          display_name: user.display_name ?? undefined,
          roles: user.roles,
        });

        if (requireAdmin && !isAdmin) {
          router.replace("/assistant");
          return;
        }

        setState({ ready: true, user, isAdmin });
      } catch (error) {
        if (cancelled || abortController.signal.aborted || isAuthRedirectError(error)) return;
        console.error("Failed to verify auth", error);
      }
    })();

    return () => {
      cancelled = true;
      abortController.abort();
    };
  }, [requireAdmin, router]);

  return state;
}
