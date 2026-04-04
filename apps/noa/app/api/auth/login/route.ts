import { NextResponse } from "next/server";

import {
  AUTH_COOKIE_NAME,
  CSRF_COOKIE_NAME,
  createCsrfToken,
  getAuthCookieOptions,
  getCsrfCookieOptions,
} from "@/components/lib/auth/server-auth";
import { getBackendBaseUrl } from "@/components/lib/http/proxy";

export async function POST(request: Request) {
  const response = await fetch(new URL("/auth/login", getBackendBaseUrl()).toString(), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: await request.text(),
    cache: "no-store",
  });

  if (!response.ok) {
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  }

  const payload = (await response.json()) as { access_token: string; expires_in: number; user: unknown };
  const next = NextResponse.json(
    { user: payload.user, expiresIn: payload.expires_in },
    { status: response.status },
  );

  next.cookies.set(AUTH_COOKIE_NAME, payload.access_token, getAuthCookieOptions());
  next.cookies.set(CSRF_COOKIE_NAME, createCsrfToken(), getCsrfCookieOptions());

  return next;
}
