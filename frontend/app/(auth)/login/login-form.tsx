"use client";

import { useSearchParams } from "next/navigation";
import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { signIn } from "@/app/actions/auth";
import { Field, FormMessage, SubmitButton } from "@/components/form";
import { Input } from "@/components/ui/input";
import type { FormState } from "@/lib/types";

const NOTICES: Record<string, string> = {
  signed_out: "You are signed out.",
  ended: "Your session has ended. Sign in again to continue.",
};

function WakeHint() {
  const { pending } = useFormStatus();
  if (!pending) return null;
  return (
    <p className="text-meta text-muted-foreground">
      If the site was idle, the server can take up to a minute to start.
    </p>
  );
}

export function LoginForm() {
  const params = useSearchParams();
  const [state, action] = useActionState<FormState, FormData>(signIn, {});
  const notice = Object.keys(NOTICES).find((key) => params.has(key));

  return (
    <form action={action} className="mt-6 space-y-4" noValidate={false}>
      {notice && !state.error && (
        <p className="rounded-md border bg-muted px-3 py-2 text-ui text-foreground">
          {NOTICES[notice]}
        </p>
      )}
      <FormMessage error={state.error} />
      <input type="hidden" name="next" value={params.get("next") ?? "/"} />
      <Field label="Email" htmlFor="email">
        <Input
          id="email"
          name="email"
          defaultValue={state.fields?.email}
          type="email"
          autoComplete="username"
          required
          autoFocus
        />
      </Field>
      <Field label="Password" htmlFor="password">
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
        />
      </Field>
      <div className="space-y-2 pt-1">
        <SubmitButton pendingLabel="Signing in…" className="w-full">
          Sign in
        </SubmitButton>
        <WakeHint />
      </div>
    </form>
  );
}
