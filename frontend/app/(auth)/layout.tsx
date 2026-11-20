import type { ReactNode } from "react";
import { GlobeLines } from "@/components/globe-lines";
import { Wordmark } from "@/components/wordmark";

/** Sign-in, first-time setup and invitations: one narrow column, no navigation. */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="relative isolate flex min-h-screen overflow-hidden px-4 py-10 sm:items-center sm:justify-center">
      <GlobeLines className="pointer-events-none absolute -right-48 -bottom-56 -z-10 size-[44rem] text-muted-foreground/[0.09] sm:-right-24 sm:-bottom-40" />
      <main id="content" className="w-full max-w-[25rem]">
        <Wordmark />
        <div className="mt-8 rounded-lg border bg-card px-6 py-7 sm:px-7">{children}</div>
      </main>
    </div>
  );
}
