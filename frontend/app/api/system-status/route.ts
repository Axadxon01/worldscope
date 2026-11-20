import { apiBaseUrl } from "@/lib/api-url";
import type { StatusResult, SystemStatus } from "@/lib/system-status";

/**
 * Proxies the backend's status endpoint so the browser never needs to know where the
 * API lives (see lib/api-url.ts for the server-only settings).
 */
const TIMEOUT_MS = 3000;

export async function GET(): Promise<Response> {
  let result: StatusResult;
  try {
    const response = await fetch(`${apiBaseUrl()}/api/v1/system/status`, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      result = { reachable: false, reason: `API answered ${response.status}` };
    } else {
      result = { reachable: true, status: (await response.json()) as SystemStatus };
    }
  } catch {
    result = { reachable: false, reason: "API not reachable" };
  }
  return Response.json(result, {
    status: result.reachable ? 200 : 502,
    headers: { "Cache-Control": "no-store" },
  });
}
