import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { sectionFor } from "@/lib/sections";

/** A section whose data does not exist yet: header plus an honest empty state. */
export function SectionPage({ href }: { href: string }) {
  const section = sectionFor(href);
  return (
    <>
      <PageHeader section={section} />
      <EmptyState section={section} />
    </>
  );
}
