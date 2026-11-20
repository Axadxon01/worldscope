"use client";

import { useActionState } from "react";
import { completeSetup } from "@/app/actions/auth";
import { Field, FormMessage, SubmitButton } from "@/components/form";
import { Input } from "@/components/ui/input";
import type { FormState } from "@/lib/types";

export function SetupForm() {
  const [state, action] = useActionState<FormState, FormData>(completeSetup, {});
  return (
    <form action={action} className="mt-6 space-y-4">
      <FormMessage error={state.error} />
      <Field
        label="Setup code"
        htmlFor="code"
        hint={
          <>
            The <code>SETUP_CODE</code> value: on Render, the worldscope-api service&apos;s
            Environment page; locally, the <code>.env</code> file.
          </>
        }
      >
        <Input
          id="code"
          name="code"
          defaultValue={state.fields?.code}
          autoComplete="off"
          spellCheck={false}
          required
        />
      </Field>
      <Field label="Your name" htmlFor="display_name">
        <Input
          id="display_name"
          name="display_name"
          defaultValue={state.fields?.display_name}
          autoComplete="name"
          maxLength={80}
          required
        />
      </Field>
      <Field label="Email" htmlFor="email">
        <Input
          id="email"
          name="email"
          defaultValue={state.fields?.email}
          type="email"
          autoComplete="username"
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
      <SubmitButton pendingLabel="Creating the account…" className="w-full">
        Create administrator account
      </SubmitButton>
    </form>
  );
}
