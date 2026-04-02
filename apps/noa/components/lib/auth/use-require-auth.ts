"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { getAuthToken } from "./auth-storage";
import { buildReturnTo } from "./return-to";

export function useRequireAuth() {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!pathname) {
      return;
    }

    const token = getAuthToken();
    if (!token) {
      const returnTo = buildReturnTo(pathname, searchParams.toString() ? `?${searchParams.toString()}` : "", window.location.hash);
      router.replace(`/login?returnTo=${encodeURIComponent(returnTo)}`);
      return;
    }

    setReady(true);
  }, [pathname, router, searchParams]);

  return ready;
}
