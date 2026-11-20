import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { LoginForm } from "@/app/(auth)/login/login-form";
import { api } from "@/lib/server/api";

export const metadata: Metadata = { title: "Sign in" };

async function FirstRunHint() {
  const result = await api<{ state: string }>("/api/v1/auth/setup", {
    timeoutMs: 20_000,
  });
  if (!result.ok || result.data.state !== "open") return null;
  return (
    <p className="mt-6 border-t pt-5 text-ui text-muted-foreground">
      No accounts exist yet.{" "}
      <Link href="/setup" className="text-primary underline-offset-4 hover:underline">
        Create the administrator account
      </Link>
      .
    </p>
  );
}

export default function LoginPage() {
  return (
    <>
      <h1 className="font-serif text-[1.75rem] leading-tight font-semibold">Sign in</h1>
      <p className="mt-2 text-muted-foreground">
        WorldScope is invite-only. Use the email address your invitation was sent to.
      </p>
      <Suspense>
        <LoginForm />
      </Suspense>
      <Suspense>
        <FirstRunHint />
      </Suspense>
    </>
  );
}
