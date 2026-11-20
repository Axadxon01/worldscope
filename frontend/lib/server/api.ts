import "server-only";
import { headers } from "next/headers";
import { apiBaseUrl } from "@/lib/api-url";

/**
 * Calls the WorldScope API from the web server. Browsers never talk to the API: they
 * talk to this app, which forwards with the internal key, the user's session token
 * and the user's IP address (for rate limits).
 */

export type ApiResult<T> =
  { ok: true; status: number; data: T } | { ok: false; status: number; detail: string };

// A free-plan API that went to sleep needs about a minute to wake up.
const DEFAULT_TIMEOUT_MS = 75_000;

export const API_UNREACHABLE =
  "The API could not be reached. If the site was idle it may still be starting up; try again in a minute.";

const FIELD_NAMES: Record<string, string> = {
  email: "Email",
  password: "Password",
  new_password: "New password",
  current_password: "Current password",
  display_name: "Name",
  code: "Setup code",
  token: "Invitation link",
  role: "Role",
};

function describeError(status: number, payload: unknown): string {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  // A bare "Forbidden" comes from the internal-key check; explain it below instead.
  if (typeof detail === "string" && detail !== "Forbidden") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    // FastAPI validation errors: name the first field that failed.
    const first = detail[0] as { loc?: unknown[]; msg?: string };
    const field = String(first.loc?.[first.loc.length - 1] ?? "");
    const message = (first.msg ?? "is not valid").replace(/^Value error, /, "");
    return `${FIELD_NAMES[field] ?? "A field"}: ${message}`;
  }
  if (status === 403) return "The web app is not allowed to call the API. Check INTERNAL_API_KEY.";
  if (status === 429) return "Too many attempts. Wait a few minutes and try again.";
  return "Something went wrong on the server. Try again.";
}

/** The visitor's IP, as reported by the hosting platform's edge (best effort). */
function clientIp(h: Headers): string | undefined {
  const candidates = [
    h.get("cf-connecting-ip"),
    h.get("true-client-ip"),
    h.get("x-forwarded-for")?.split(",")[0],
    h.get("x-real-ip"),
  ];
  return candidates.map((value) => value?.trim()).find(Boolean) ?? undefined;
}

export async function api<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    token?: string | null;
    timeoutMs?: number;
  } = {},
): Promise<ApiResult<T>> {
  const incoming = await headers();
  const outgoing: Record<string, string> = { Accept: "application/json" };
  const key = process.env.INTERNAL_API_KEY?.trim();
  if (key) outgoing["X-Internal-Key"] = key;
  const ip = clientIp(incoming);
  if (ip) outgoing["X-Client-IP"] = ip;
  const agent = incoming.get("user-agent");
  if (agent) outgoing["X-Client-User-Agent"] = agent.slice(0, 400);
  if (options.token) outgoing.Authorization = `Bearer ${options.token}`;
  if (options.body !== undefined) outgoing["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(`${apiBaseUrl()}${path}`, {
      method: options.method ?? "GET",
      headers: outgoing,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: "no-store",
      signal: AbortSignal.timeout(options.timeoutMs ?? DEFAULT_TIMEOUT_MS),
    });
  } catch {
    return { ok: false, status: 0, detail: API_UNREACHABLE };
  }

  if (response.status === 204) return { ok: true, status: 204, data: undefined as T };
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (response.ok) return { ok: true, status: response.status, data: payload as T };
  return {
    ok: false,
    status: response.status,
    detail: describeError(response.status, payload),
  };
}
