"use client";

import type { ReactNode } from "react";
import { useFormStatus } from "react-dom";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function Field({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-ui font-medium text-foreground">
        {label}
      </label>
      {children}
      {hint && <p className="text-meta text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** Errors and confirmations from a form action, announced to screen readers. */
export function FormMessage({ error, ok }: { error?: string; ok?: string }) {
  return (
    <div aria-live="polite" className="min-h-0">
      {error && (
        <p
          role="alert"
          className="rounded-md border border-status-critical/40 bg-status-critical/10 px-3 py-2 text-ui text-foreground"
        >
          {error}
        </p>
      )}
      {ok && (
        <p className="rounded-md border border-status-good/40 bg-status-good/10 px-3 py-2 text-ui text-foreground">
          {ok}
        </p>
      )}
    </div>
  );
}

export function SubmitButton({
  children,
  pendingLabel,
  variant = "default",
  className,
}: {
  children: ReactNode;
  pendingLabel: string;
  variant?: "default" | "outline" | "destructive" | "ghost";
  className?: string;
}) {
  const { pending } = useFormStatus();
  return (
    <Button
      type="submit"
      variant={variant}
      disabled={pending}
      aria-disabled={pending}
      className={cn("h-10 px-4 text-[0.9375rem]", className)}
    >
      {pending ? pendingLabel : children}
    </Button>
  );
}
