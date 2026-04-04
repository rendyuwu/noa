const DEFAULT_RETURN_TO = "/assistant";

export function sanitizeReturnTo(value: string | null | undefined) {
  if (!value) {
    return DEFAULT_RETURN_TO;
  }

  if (!value.startsWith("/") || value.startsWith("//")) {
    return DEFAULT_RETURN_TO;
  }

  return value;
}

export function buildReturnTo(pathname: string, search = "", hash = "") {
  const nextPath = `${pathname}${search}${hash}`;
  return sanitizeReturnTo(nextPath);
}
