/** Shapes returned by the WorldScope API (see backend/worldscope/api/schemas.py). */

export type Role = "admin" | "analyst";

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  created_at: string;
  last_login_at: string | null;
  disabled: boolean;
}

export interface Me {
  user: User;
  session_expires_at: string;
}

export interface SignedIn {
  token: string;
  expires_at: string;
  user: User;
}

export interface Invitation {
  id: string;
  email: string;
  role: Role;
  status: "pending" | "used" | "revoked" | "expired";
  created_at: string;
  expires_at: string;
  invited_by: string | null;
}

export interface InvitationPreview {
  email: string;
  role: Role;
  expires_at: string;
}

export interface Source {
  slug: string;
  name: string;
  type: "rss" | "official" | "api";
  url: string;
  homepage: string;
  publisher_country: { iso3: string; name: string } | null;
  language: string;
  focus: string[];
  ownership: string | null;
  is_primary: boolean;
  licence: string | null;
  storage_policy: "link_only" | "snippet";
  terms_url: string | null;
  review_status: "pending" | "approved" | "rejected";
  review_note: string | null;
  enabled: boolean;
  last_success_at: string | null;
  consecutive_failures: number;
}

/** What every form action returns to its form. */
export interface FormState {
  error?: string;
  ok?: string;
  /** Non-secret values to put back after an error: React clears a form after each submit. */
  fields?: Record<string, string>;
}
