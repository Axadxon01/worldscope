"use client";

import { useActionState } from "react";
import { changePassword } from "@/app/actions/auth";
import { Field, FormMessage, SubmitButton } from "@/components/form";
import { Input } from "@/components/ui/input";
import type { FormState } from "@/lib/types";

export function PasswordForm() {
  const [state, action] = useActionState<FormState, FormData>(changePassword, {});
  return (
    <form action={action} className="space-y-4" key={state.ok ? "done" : "editing"}>
      <FormMessage error={state.error} ok={state.ok} />
      <Field label="Current password" htmlFor="current_password">
        <Input
          id="current_password"
          name="current_password"
          type="password"
          autoComplete="current-password"
          required
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="New password" htmlFor="new_password" hint="At least 12 characters.">
          <Input
            id="new_password"
            name="new_password"
            type="password"
            autoComplete="new-password"
            minLength={12}
            required
          />
        </Field>
        <Field label="New password again" htmlFor="confirm">
          <Input
            id="confirm"
            name="confirm"
            type="password"
            autoComplete="new-password"
            minLength={12}
            required
          />
        </Field>
      </div>
      <SubmitButton variant="outline" pendingLabel="Changing…">
        Change password
      </SubmitButton>
    </form>
  );
}
