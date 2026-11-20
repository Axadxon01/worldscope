import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { SetupForm } from "@/app/(auth)/setup/setup-form";
import { api } from "@/lib/server/api";

export const metadata: Metadata = { title: "Set up WorldScope" };

async function SetupState() {
  const result = await api<{ state: "open" | "closed" | "unconfigured" }>("/api/v1/auth/setup");
  if (!result.ok) {
    return <p className="mt-4 text-muted-foreground">{result.detail}</p>;
  }
  if (result.data.state === "closed") {
    return (
      <p className="mt-4 text-muted-foreground">
        Setup is complete: an administrator account already exists.{" "}
        <Link href="/login" className="text-primary underline-offset-4 hover:underline">
          Sign in
        </Link>{" "}
        or ask an administrator for an invitation.
      </p>
    );
  }
  if (result.data.state === "unconfigured") {
    return (
      <p className="mt-4 text-muted-foreground">
        Setup is turned off because no setup code is configured. Set <code>SETUP_CODE</code> on the
        API and restart it, or create an invitation from the command line with{" "}
        <code>python -m worldscope.cli invite</code>.
      </p>
    );
  }
  return <SetupForm />;
}

export default function SetupPage() {
  return (
    <>
      <h1 className="font-serif text-[1.75rem] leading-tight font-semibold">
        Create the administrator
      </h1>
      <p className="mt-2 text-muted-foreground">
        The first account runs WorldScope and invites everyone else. This page closes once it
        exists.
      </p>
      <Suspense fallback={<p className="mt-4 text-muted-foreground">Checking setup…</p>}>
        <SetupState />
      </Suspense>
    </>
  );
}
