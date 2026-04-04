/**
 * Edge-safe auth constants. Import from here in Edge middleware/proxy code.
 * In Node.js contexts, prefer importing via server-auth.ts.
 */
export const AUTH_COOKIE_NAME = "noa_session";
export const CSRF_COOKIE_NAME = "noa_csrf";
export const AUTH_COOKIE_MAX_AGE_SECONDS = 60 * 60;
