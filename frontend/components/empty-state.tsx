import type { Section } from "@/lib/sections";

/**
 * What a page says before its data exists: why it is empty, what it will show, and
 * which milestone brings it. No sample or placeholder data is ever shown instead.
 */
export function EmptyState({
  section,
  title = "Nothing to show yet",
}: {
  section: Section;
  title?: string;
}) {
  return (
    <section
      aria-labelledby="empty-heading"
      className="mt-8 max-w-[44rem] rounded-lg border bg-card px-6 py-6"
    >
      <h2 id="empty-heading" className="text-ui font-semibold text-foreground">
        {title}
      </h2>
      <p className="mt-1.5 text-muted-foreground">
        {section.emptyBecause} The full page arrives in Milestone {section.milestone}.
      </p>

      {section.willShow.length > 0 && (
        <>
          <p className="mt-5 text-foreground">When it is ready, this page shows:</p>
          <ul className="mt-2 space-y-1.5">
            {section.willShow.map((item) => (
              <li key={item} className="flex gap-3 text-muted-foreground">
                <span aria-hidden className="mt-[0.6rem] h-px w-3 shrink-0 bg-border" />
                {item}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
