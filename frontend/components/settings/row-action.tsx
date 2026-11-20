"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import type { FormState } from "@/lib/types";

function Submit({ label, pendingLabel }: { label: string; pendingLabel: string }) {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      disabled={pending}
      className="rounded-sm text-ui text-muted-foreground underline-offset-4 hover:text-foreground hover:underline disabled:opacity-60"
    >
      {pending ? pendingLabel : label}
    </button>
  );
}

/** A one-button form on a table row (revoke, disable), with its error beside it. */
export function RowAction({
  action,
  fields,
  label,
  pendingLabel,
}: {
  action: (state: FormState, form: FormData) => Promise<FormState>;
  fields: Record<string, string>;
  label: string;
  pendingLabel: string;
}) {
  const [state, formAction] = useActionState(action, {});
  return (
    <form action={formAction} className="text-right">
      {Object.entries(fields).map(([name, value]) => (
        <input key={name} type="hidden" name={name} value={value} />
      ))}
      <Submit label={label} pendingLabel={pendingLabel} />
      {state.error && (
        <p role="alert" className="mt-1 max-w-56 text-meta text-status-critical">
          {state.error}
        </p>
      )}
    </form>
  );
}
