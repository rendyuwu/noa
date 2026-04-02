"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { ApiError, fetchWithAuth, jsonOrThrow } from "@/components/lib/http/fetch-client";
import { reportClientError } from "@/components/lib/observability/error-reporting";

import { clearAuth, getAuthToken, getAuthUser, setAuthUser } from "./auth-storage";
import { buildReturnTo } from "./return-to";
import type { AuthUser } from "./types";

type AuthMeResponse = {
  user: AuthUser | null;
};

export type AuthSessionState = {
  error: string | null;
  ready: boolean;
  refresh: () => Promise<AuthUser | null>;
  user: AuthUser | null;
  validating: boolean;
};

function getCurrentReturnTo(pathname: string | null) {
  if (typeof window === "undefined") {
    return pathname ?? "/assistant";
  }

  return buildReturnTo(pathname ?? window.location.pathname, window.location.search, window.location.hash);
}

function isAuthorizationFailure(error: unknown) {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

export function useAuthSession(): AuthSessionState {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(() => getAuthUser());
  const [validating, setValidating] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  const redirectToLogin = useCallback(() => {
    const returnTo = getCurrentReturnTo(pathname);
    router.replace(`/login?returnTo=${encodeURIComponent(returnTo)}`);
  }, [pathname, router]);

  const refresh = useCallback(async () => {
    const token = getAuthToken();
    if (!token) {
      setAuthUser(null);
      setUser(null);
      setError(null);
      setValidating(false);
      redirectToLogin();
      return null;
    }

    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setValidating(true);
    setError(null);

    try {
      const response = await fetchWithAuth("/auth/me", {
        cache: "no-store",
      });
      const payload = await jsonOrThrow<AuthMeResponse>(response);
      if (requestIdRef.current != requestId) {
        return payload.user;
      }

      setAuthUser(payload.user);
      setUser(payload.user);
      setValidating(false);
      return payload.user;
    } catch (caughtError) {
      if (requestIdRef.current != requestId) {
        return null;
      }

      if (isAuthorizationFailure(caughtError)) {
        clearAuth({ returnTo: getCurrentReturnTo(pathname), redirect: true });
        setAuthUser(null);
        setUser(null);
        setError(null);
        setValidating(false);
        return null;
      }

      reportClientError(caughtError, {
        source: "auth.session.refresh",
      });
      setAuthUser(null);
      setUser(null);
      setError("We couldn't verify your session. Retry to continue.");
      setValidating(false);
      return null;
    }
  }, [pathname, redirectToLogin]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return useMemo(
    () => ({
      error,
      ready: !validating && error === null && user !== null,
      refresh,
      user,
      validating,
    }),
    [error, refresh, user, validating],
  );
}
