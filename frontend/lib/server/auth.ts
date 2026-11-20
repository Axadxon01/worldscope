import "server-only";
import { redirect } from "next/navigation";
import { cache } from "react";
import { api } from "@/lib/server/api";
import { sessionToken } from "@/lib/server/session";
import type { Me } from "@/lib/types";

/**
 * The signed-in user, verified by the API on every request (deduplicated within one
 * render). No session: go to sign-in. A session the API no longer accepts: clear the
 * cookie and say so. API unreachable: throw, so the error boundary explains it rather
 * than signing the person out.
 */
export const getMe = cache(async (): Promise<Me> => {
  const token = await sessionToken();
  if (!token) redirect("/login");
  const result = await api<Me>("/api/v1/auth/me", { token });
  if (result.ok) return result.data;
  if (result.status === 401) redirect("/auth/session-ended");
  throw new Error(result.detail);
});

/** For server actions: the session token, or a redirect to sign-in. */
export async function requireToken(): Promise<string> {
  const token = await sessionToken();
  if (!token) redirect("/login");
  return token;
}
