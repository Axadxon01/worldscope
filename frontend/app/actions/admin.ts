"use server";

import { refresh } from "next/cache";
import { headers } from "next/headers";
import { api } from "@/lib/server/api";
import { requireToken } from "@/lib/server/auth";
import type { FormState, Invitation } from "@/lib/types";

export interface InviteState extends FormState {
  link?: string;
  email?: string;
}

/** The address people use to reach this app, for building invitation links. */
async function siteOrigin(): Promise<string> {
  const h = await headers();
  const host = h.get("x-forwarded-host") ?? h.get("host") ?? "localhost:3000";
  const proto = h.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  return `${proto.split(",")[0].trim()}://${host.split(",")[0].trim()}`;
}

export async function createInvitation(_: InviteState, form: FormData): Promise<InviteState> {
  const token = await requireToken();
  const email = String(form.get("email") ?? "").trim();
  const role = form.get("role") === "admin" ? "admin" : "analyst";
  const result = await api<{ invitation: Invitation; token: string }>("/api/v1/admin/invitations", {
    method: "POST",
    token,
    body: { email, role },
  });
  if (!result.ok) return { error: result.detail, fields: { email, role } };
  refresh();
  // The token goes after "#": browsers never send that part to a server, so the link
  // stays out of access logs on its way to the invitation page.
  return {
    link: `${await siteOrigin()}/invite#${result.data.token}`,
    email: result.data.invitation.email,
  };
}

export async function revokeInvitation(_: FormState, form: FormData): Promise<FormState> {
  const token = await requireToken();
  const id = encodeURIComponent(String(form.get("id") ?? ""));
  const result = await api(`/api/v1/admin/invitations/${id}`, {
    method: "DELETE",
    token,
  });
  if (!result.ok) return { error: result.detail };
  refresh();
  return {};
}

export async function setUserDisabled(_: FormState, form: FormData): Promise<FormState> {
  const token = await requireToken();
  const id = encodeURIComponent(String(form.get("id") ?? ""));
  const action = form.get("disabled") === "true" ? "disable" : "enable";
  const result = await api(`/api/v1/admin/users/${id}/${action}`, {
    method: "POST",
    token,
  });
  if (!result.ok) return { error: result.detail };
  refresh();
  return {};
}
