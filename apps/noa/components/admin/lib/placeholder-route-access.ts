export function isPlaceholderAdminRouteEnabled(env: NodeJS.ProcessEnv = process.env) {
  if (
    env.NOA_ENABLE_PLACEHOLDER_ADMIN_SURFACES === "true" ||
    env.NOA_ENABLE_PREVIEW_ADMIN_ROUTES === "true"
  ) {
    return true;
  }

  return env.NODE_ENV !== "production";
}
