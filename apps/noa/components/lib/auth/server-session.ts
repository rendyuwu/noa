import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { getBackendBaseUrl } from "@/components/lib/http/proxy";

import { AUTH_COOKIE_NAME } from "./server-auth";
import { sanitizeReturnTo } from "./return-to";
import type { AuthUser } from "./types";

type AuthMeResponse = { user: AuthUser | null };

export async function fetchServerAuthUser(): Promise<AuthUser | null> {
  const token = (await cookies()).get(AUTH_COOKIE_NAME)?.value;
  if (!token) return null;

  const response = await fetch(new URL("/auth/me", getBackendBaseUrl()).toString(), {
    headers: { authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  if (!response.ok) {
    return null;
  }

  const payload = (await response.json()) as AuthMeResponse;
  return payload.user;
}

export async function requireServerUser(returnTo: string): Promise<AuthUser> {
  const user = await fetchServerAuthUser();
  if (!user) {
    redirect(`/login?returnTo=${encodeURIComponent(sanitizeReturnTo(returnTo))}`);
  }

  return user;
}

export async function requireServerAdmin(returnTo: string, prefetchedUser?: AuthUser | null): Promise<AuthUser> {
  const user = prefetchedUser ?? (await requireServerUser(returnTo));
  if (!(user.roles ?? []).includes("admin")) {
    redirect("/assistant");
  }

  return user;
}
