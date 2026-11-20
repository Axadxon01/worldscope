"use client";

import { Check, Copy } from "lucide-react";
import { useActionState, useState } from "react";
import { createInvitation, type InviteState } from "@/app/actions/admin";
import { Field, FormMessage, SubmitButton } from "@/components/form";
import { Input, Select } from "@/components/ui/input";

function InviteLink({ email, link }: { email: string; link: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded-md border border-status-good/40 bg-status-good/10 px-4 py-3">
      <p className="text-ui text-foreground">
        Invitation created for {email}. Send them this link; it works once, for 7 days, and is not
        shown again.
      </p>
      <div className="mt-2.5 flex gap-2">
        <Input readOnly value={link} aria-label="Invitation link" className="font-mono text-meta" />
        <button
          type="button"
          onClick={async () => {
            await navigator.clipboard.writeText(link);
            setCopied(true);
          }}
          className="inline-flex h-10 shrink-0 items-center gap-1.5 rounded-md border px-3 text-ui hover:bg-accent"
        >
          {copied ? (
            <Check aria-hidden className="size-4" />
          ) : (
            <Copy aria-hidden className="size-4" />
          )}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

export function InviteForm() {
  const [state, action] = useActionState<InviteState, FormData>(createInvitation, {});
  return (
    <div className="space-y-4">
      {state.link && state.email && <InviteLink email={state.email} link={state.link} />}
      <form action={action} className="space-y-4" key={state.link ?? "new"}>
        <FormMessage error={state.error} />
        <div className="grid gap-4 sm:grid-cols-[1fr_11rem]">
          <Field label="Email" htmlFor="invite-email">
            <Input
              id="invite-email"
              name="email"
              defaultValue={state.fields?.email}
              type="email"
              autoComplete="off"
              required
            />
          </Field>
          <Field label="Role" htmlFor="invite-role">
            <Select id="invite-role" name="role" defaultValue={state.fields?.role ?? "analyst"}>
              <option value="analyst">Analyst</option>
              <option value="admin">Administrator</option>
            </Select>
          </Field>
        </div>
        <SubmitButton variant="outline" pendingLabel="Creating invitation…">
          Create invitation
        </SubmitButton>
      </form>
    </div>
  );
}
