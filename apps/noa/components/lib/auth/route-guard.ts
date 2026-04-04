import { sanitizeReturnTo } from "./return-to";

export function isProtectedPath(pathname: string) {
  return (
    pathname === "/assistant" ||
    pathname.startsWith("/assistant/") ||
    pathname === "/admin" ||
    pathname.startsWith("/admin/")
  );
}

export function isAdminPath(pathname: string) {
  return pathname === "/admin" || pathname.startsWith("/admin/");
}

export function buildLoginRedirect(rawUrl: string) {
  const url = new URL(rawUrl);
  const returnTo = sanitizeReturnTo(`${url.pathname}${url.search}`);
  return `/login?returnTo=${encodeURIComponent(returnTo)}`;
}
