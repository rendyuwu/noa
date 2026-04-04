import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import { AUTH_COOKIE_NAME } from "@/components/lib/auth/server-auth";
import { buildLoginRedirect, isProtectedPath } from "@/components/lib/auth/route-guard";

export function middleware(request: NextRequest) {
  if (!isProtectedPath(request.nextUrl.pathname)) {
    return NextResponse.next();
  }

  if (request.cookies.get(AUTH_COOKIE_NAME)?.value) {
    return NextResponse.next();
  }

  return NextResponse.redirect(new URL(buildLoginRedirect(request.url), request.url));
}

export const config = {
  matcher: ["/assistant/:path*", "/admin/:path*"],
};
