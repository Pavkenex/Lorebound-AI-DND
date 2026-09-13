/** API base normalization — kept dependency-free so `node --test` can load it directly.
 *
 *  The live deploy passes NEXT_PUBLIC_API_URL without a scheme ("host:port"), which makes
 *  fetch() resolve the string as a *relative* URL and hit the frontend origin instead of
 *  the backend (sign-in showed "The gate held…" while every request 404'd on :3000). */

/** Trim, add "http://" when the scheme is missing, drop trailing slashes. "" when empty. */
export function normalizeApiBase(raw: string | null | undefined): string {
  const v = (raw ?? "").trim().replace(/\/+$/, "");
  if (!v) return "";
  return /^https?:\/\//i.test(v) ? v : `http://${v}`;
}
