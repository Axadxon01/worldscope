"use client";

import Link from "next/link";
import { useActionState, useEffect, useState, useSyncExternalStore } from "react";
import { acceptInvitation, previewInvitation, type InvitationLookup } from "@/app/actions/auth";
import { Field, FormMessage, SubmitButton } from "@/components/form";
import { Input } from "@/components/ui/input";
import type { FormState } from "@/lib/types";

const ROLE_LABEL = {
  admin: "an administrator",
  analyst: "an analyst",
} as const;

function subscribeToHash(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

const readHash = () => window.location.hash.slice(1);
const noHashOnServer = () => null;

/**
 * The invitation token arrives after "#" in the link, which browsers never send to a
 * server, so it is read here and checked with the API before the form appears.
 */
export function InviteForm() {
  const token = useSyncExternalStore(subscribeToHash, readHash, noHashOnServer);
  const [lookup, setLookup] = useState<InvitationLookup | null>(null);
  const [state, action] = useActionState<FormState, FormData>(acceptInvitation, {});

  useEffect(() => {
    if (token) previewInvitation(token).then(setLookup);
  }, [token]);

  const problem =
    token === ""
      ? "This link has no invitation in it. Use the full link you were sent."
      : lookup && !lookup.ok
        ? lookup.error
        : null;

  if (problem) {
    return (
      <>
        <p className="mt-3 text-muted-foreground">{problem}</p>
        <p className="mt-5 text-ui">
          <Link href="/login" className="text-primary underline-offset-4 hover:underline">
            Go to sign in
          </Link>
        </p>
      </>
    );
  }

  if (token === null || lookup === null || !lookup.ok) {
    return <p className="mt-3 text-muted-foreground">Checking your invitation…</p>;
  }

  const { invitation } = lookup;
  return (
    <>
      <p className="mt-2 text-muted-foreground">
        You are invited as {ROLE_LABEL[invitation.role]}. Your account will use{" "}
        <span className="text-foreground">{invitation.email}</span>.
      </p>
      <form action={action} className="mt-6 space-y-4">
        <FormMessage error={state.error} />
        <input type="hidden" name="token" value={token} />
        <input type="hidden" name="email" value={invitation.email} autoComplete="username" />
        <Field label="Your name" htmlFor="display_name" hint="Shown to other people in WorldScope.">
          <Input
            id="display_name"
            name="display_name"
            defaultValue={state.fields?.display_name}
            autoComplete="name"
            maxLength={80}
            required
          />
        </Field>
        <Field
          label="Password"
          htmlFor="password"
          hint="At least 12 characters. A few words work well."
        >
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="new-password"
            minLength={12}
            required
          />
        </Field>
        <Field label="Password again" htmlFor="confirm">
          <Input
            id="confirm"
            name="confirm"
            type="password"
            autoComplete="new-password"
            minLength={12}
            required
          />
        </Field>
        <SubmitButton pendingLabel="Creating your account…" className="w-full">
          Create account
        </SubmitButton>
      </form>
    </>
  );
}
