import type { Metadata } from "next";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { StatusPanel } from "@/components/status-panel";
import { sectionFor } from "@/lib/sections";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  const section = sectionFor("/settings");
  return (
    <>
      <PageHeader section={section} />

      <section aria-labelledby="platform-heading" className="mt-8 max-w-[44rem]">
        <h2 id="platform-heading" className="font-serif text-[1.3125rem] font-semibold">
          Platform status
        </h2>
        <p className="mt-1.5 text-muted-foreground">
          Live checks of each service. A worker heartbeat arrives every minute once the
          scheduler and a worker are running.
        </p>
        <div className="mt-4 rounded-lg border bg-card px-5 py-4">
          <StatusPanel detailed />
        </div>
      </section>

      <EmptyState section={section} title="Your account" />
    </>
  );
}
