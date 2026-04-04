import { randomBytes, timingSafeEqual } from "node:crypto";

import { AUTH_COOKIE_MAX_AGE_SECONDS } from "./auth-constants";

export { AUTH_COOKIE_MAX_AGE_SECONDS, AUTH_COOKIE_NAME, CSRF_COOKIE_NAME } from "./auth-constants";

export function createCsrfToken() {
  return randomBytes(24).toString("base64url");
}

export function isMutationMethod(method: string) {
  return ["POST", "PUT", "PATCH", "DELETE"].includes(method.toUpperCase());
}

export function tokensMatch(cookieToken: string | null, headerToken: string | null) {
  if (!cookieToken || !headerToken) return false;

  const left = Buffer.from(cookieToken);
  const right = Buffer.from(headerToken);
  if (left.length !== right.length) return false;

  return timingSafeEqual(left, right);
}

export function getAuthCookieOptions() {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: AUTH_COOKIE_MAX_AGE_SECONDS,
  };
}

export function getCsrfCookieOptions() {
  return {
    httpOnly: false,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: AUTH_COOKIE_MAX_AGE_SECONDS,
  };
}
