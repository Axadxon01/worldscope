/**
 * Where the web server reaches the API. Server-only: never exposed to the browser.
 *
 * - API_INTERNAL_URL: a full URL, e.g. http://api:8000 (Docker Compose)
 * - API_INTERNAL_HOSTPORT: host:port on a private network, e.g. what Render's
 *   Blueprint provides for another service; reached over plain HTTP inside that network
 */
export function apiBaseUrl(env: Record<string, string | undefined> = process.env): string {
  const url = env.API_INTERNAL_URL?.trim();
  if (url) return url.replace(/\/+$/, "");
  const hostport = env.API_INTERNAL_HOSTPORT?.trim();
  if (hostport) return `http://${hostport}`;
  return "http://localhost:8000";
}
