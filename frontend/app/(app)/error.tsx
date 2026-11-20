"use client";

import { Button } from "@/components/ui/button";

/** Shown when a page could not load its data, most often while the API is starting. */
export default function AppError({ reset }: { error: Error; reset: () => void }) {
  return (
    <section
      className="max-w-[44rem] rounded-lg border bg-card px-6 py-6"
      aria-labelledby="error-heading"
    >
      <h1 id="error-heading" className="font-serif text-[1.3125rem] font-semibold">
        This page could not load
      </h1>
      <p className="mt-2 text-muted-foreground">
        The API did not answer. On the free hosting plan it sleeps after 15 minutes without visits
        and takes about a minute to start again.
      </p>
      <Button onClick={reset} className="mt-5 h-10 px-4 text-[0.9375rem]">
        Try again
      </Button>
    </section>
  );
}
