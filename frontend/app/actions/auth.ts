"use server";

import { redirect } from "next/navigation";
import { api } from "@/lib/server/api";
import { requireToken } from "@/lib/server/auth";
import { clearSession, safeNext, storeSession } from "@/lib/server/session";
import type { FormState, InvitationPreview, SignedIn } from "@/lib/types";

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value : "";
}

function passwordsMatch(form: FormData): string | undefined {
  return text(form, "password") === text(form, "confirm")
    ? undefined
    : "The two passwords do not match.";
}

function keep(form: FormData, ...names: string[]): Record<string, string> {
  return Object.fromEntries(names.map((name) => [name, text(form, name)]));
}

export async function signIn(_: FormState, form: FormData): Promise<FormState> {
  const result = await api<SignedIn>("/api/v1/auth/login", {
    method: "POST",
    body: {
      email: text(form, "email").trim(),
      password: text(form, "password"),
    },
  });
  if (!result.ok) return { error: result.detail, fields: keep(form, "email") };
  await storeSession(result.data.token);
  redirect(safeNext(text(form, "next")));
}

export async function signOut(): Promise<void> {
  const token = await requireToken();
  // Sign out locally even if the API cannot be reached; the session then expires.
  await api("/api/v1/auth/logout", {
    method: "POST",
    token,
    timeoutMs: 15_000,
  });
  await clearSession();
  redirect("/login?signed_out=1");
}

export async function completeSetup(_: FormState, form: FormData): Promise<FormState> {
  const fields = keep(form, "code", "display_name", "email");
  const mismatch = passwordsMatch(form);
  if (mismatch) return { error: mismatch, fields };
  const result = await api<SignedIn>("/api/v1/auth/setup", {
    method: "POST",
    body: {
      code: text(form, "code").trim(),
      email: text(form, "email").trim(),
      display_name: text(form, "display_name"),
      password: text(form, "password"),
    },
  });
  if (!result.ok) return { error: result.detail, fields };
  await storeSession(result.data.token);
  redirect("/settings");
}

export type InvitationLookup =
  { ok: true; invitation: InvitationPreview } | { ok: false; error: string };

export async function previewInvitation(token: string): Promise<InvitationLookup> {
  const result = await api<InvitationPreview>("/api/v1/invitations/preview", {
    method: "POST",
    body: { token: token.slice(0, 128) },
  });
  return result.ok ? { ok: true, invitation: result.data } : { ok: false, error: result.detail };
}

export async function acceptInvitation(_: FormState, form: FormData): Promise<FormState> {
  const fields = keep(form, "display_name");
  const mismatch = passwordsMatch(form);
  if (mismatch) return { error: mismatch, fields };
  const result = await api<SignedIn>("/api/v1/invitations/accept", {
    method: "POST",
    body: {
      token: text(form, "token"),
      display_name: text(form, "display_name"),
      password: text(form, "password"),
    },
  });
  if (!result.ok) return { error: result.detail, fields };
  await storeSession(result.data.token);
  redirect("/");
}

export async function changePassword(_: FormState, form: FormData): Promise<FormState> {
  if (text(form, "new_password") !== text(form, "confirm")) {
    return { error: "The two new passwords do not match." };
  }
  const token = await requireToken();
  const result = await api("/api/v1/auth/password", {
    method: "POST",
    token,
    body: {
      current_password: text(form, "current_password"),
      new_password: text(form, "new_password"),
    },
  });
  if (!result.ok) return { error: result.detail };
  return {
    ok: "Password changed. Other browsers signed in to your account were signed out.",
  };
}
