import { NextResponse } from "next/server";

import { AUTH_COOKIE_NAME, CSRF_COOKIE_NAME } from "@/components/lib/auth/server-auth";

export async function POST() {
  const response = NextResponse.json({ ok: true });
  response.cookies.set(AUTH_COOKIE_NAME, "", { path: "/", maxAge: 0 });
  response.cookies.set(CSRF_COOKIE_NAME, "", { path: "/", maxAge: 0 });
  return response;
}
