import { NextResponse } from "next/server";

import {
  AUTH_COOKIE_NAME,
  CSRF_COOKIE_NAME,
  createCsrfToken,
  getCsrfCookieOptions,
} from "@/components/lib/auth/server-auth";
import { getBackendBaseUrl, getCookieValue } from "@/components/lib/http/proxy";

export async function GET(request: Request) {
  const token = getCookieValue(request.headers, AUTH_COOKIE_NAME);

  if (!token) {
    return NextResponse.json({ detail: "Missing bearer token", error_code: "missing_bearer_token" }, { status: 401 });
  }

  const upstream = await fetch(new URL("/auth/me", getBackendBaseUrl()).toString(), {
    headers: { authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  const body = await upstream.text();
  const next = new NextResponse(body, {
    status: upstream.status,
    headers: { "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });

  if (upstream.ok && !getCookieValue(request.headers, CSRF_COOKIE_NAME)) {
    next.cookies.set(CSRF_COOKIE_NAME, createCsrfToken(), getCsrfCookieOptions());
  }

  if (upstream.status === 401 || upstream.status === 403) {
    next.cookies.set(AUTH_COOKIE_NAME, "", { path: "/", maxAge: 0 });
    next.cookies.set(CSRF_COOKIE_NAME, "", { path: "/", maxAge: 0 });
  }

  return next;
}
